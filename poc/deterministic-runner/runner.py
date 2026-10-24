from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence


StageStatus = Literal["passed", "reused", "failed", "timed_out"]
RunStatus = Literal["passed", "failed"]
OUTPUT_TAIL_CHARACTERS = 4_000


@dataclass(frozen=True)
class StageSpec:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float
    required_outputs: tuple[Path, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ValueError("Stage name must use lowercase letters, digits, and underscores")
        if not self.argv or any(not value for value in self.argv):
            raise ValueError("Stage argv must contain non-empty arguments")
        if self.timeout_seconds <= 0:
            raise ValueError("Stage timeout_seconds must be positive")
        environment_names = [name for name, _ in self.environment]
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) for name in environment_names):
            raise ValueError("Stage environment names must be non-empty uppercase identifiers")
        if len(environment_names) != len(set(environment_names)):
            raise ValueError("Stage environment names must be unique")

    @property
    def fingerprint(self) -> str:
        value = {
            "argv": self.argv,
            "timeout_seconds": self.timeout_seconds,
            "required_outputs": tuple(str(path) for path in self.required_outputs),
            "environment": self.environment,
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class ExecutionOutcome:
    exit_code: int
    stdout: str
    stderr: str


class CommandExecutor(Protocol):
    def execute(self, spec: StageSpec, cwd: Path) -> ExecutionOutcome: ...


class StageTimedOut(RuntimeError):
    def __init__(self, timeout_seconds: float, stdout: str, stderr: str) -> None:
        super().__init__(f"stage exceeded {timeout_seconds} second timeout")
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr


class SubprocessExecutor:
    def execute(self, spec: StageSpec, cwd: Path) -> ExecutionOutcome:
        environment = os.environ.copy()
        environment.update(dict(spec.environment))
        try:
            completed = subprocess.run(
                spec.argv,
                cwd=cwd,
                capture_output=True,
                check=False,
                text=True,
                timeout=spec.timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise StageTimedOut(
                spec.timeout_seconds,
                _decoded_timeout_output(error.stdout),
                _decoded_timeout_output(error.stderr),
            ) from error
        return ExecutionOutcome(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StageResult:
    name: str
    status: StageStatus
    fingerprint: str
    started_at_utc: str
    completed_at_utc: str
    duration_ms: int
    exit_code: int | None
    stdout_log: str
    stderr_log: str
    stdout_tail: str
    stderr_tail: str
    artifacts: tuple[ArtifactRecord, ...]
    error: str


@dataclass(frozen=True)
class PipelineResult:
    schema_version: int
    run_id: str
    status: RunStatus
    started_at_utc: str
    completed_at_utc: str
    duration_ms: int
    cwd: str
    metadata: Mapping[str, Any]
    stages: tuple[StageResult, ...]

    def to_json_dict(self) -> Mapping[str, Any]:
        return asdict(self)


class PipelineFailed(RuntimeError):
    def __init__(self, result: PipelineResult) -> None:
        failed_stage = result.stages[-1]
        super().__init__(f"Stage {failed_stage.name} {failed_stage.status}: {failed_stage.error}")
        self.result = result


class PipelineRunner:
    def __init__(
        self,
        executor: CommandExecutor,
        cwd: Path,
        manifest_path: Path,
    ) -> None:
        if not cwd.is_dir():
            raise ValueError(f"Pipeline cwd is not a directory: {cwd}")
        self._executor = executor
        self._cwd = cwd.resolve()
        self._manifest_path = manifest_path.resolve()

    def run(
        self,
        stages: Sequence[StageSpec],
        *,
        run_id: str,
        metadata: Mapping[str, Any],
        resume: bool = False,
    ) -> PipelineResult:
        if not stages:
            raise ValueError("Pipeline requires at least one stage")
        if not run_id.strip():
            raise ValueError("run_id cannot be empty")
        previous = self._load_previous_stages() if resume else {}
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        results: list[StageResult] = []

        for spec in stages:
            prior = previous.get(spec.name)
            if prior is not None and self._can_reuse(spec, prior):
                stage_result = self._reused_result(spec, prior)
            else:
                stage_result = self._run_stage(spec)
            results.append(stage_result)
            status: RunStatus = "passed" if stage_result.status in {"passed", "reused"} else "failed"
            result = self._build_result(
                run_id=run_id,
                status=status,
                started_at=started_at,
                started_monotonic=started_monotonic,
                metadata=metadata,
                stages=results,
            )
            self._write_manifest(result)
            if status == "failed":
                raise PipelineFailed(result)

        return self._build_result(
            run_id=run_id,
            status="passed",
            started_at=started_at,
            started_monotonic=started_monotonic,
            metadata=metadata,
            stages=results,
        )

    def _run_stage(self, spec: StageSpec) -> StageResult:
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        outcome: ExecutionOutcome
        try:
            outcome = self._executor.execute(spec, self._cwd)
        except StageTimedOut as error:
            return self._finish_stage(
                spec=spec,
                status="timed_out",
                started_at=started_at,
                started_monotonic=started_monotonic,
                exit_code=None,
                stdout=error.stdout,
                stderr=error.stderr,
                artifacts=(),
                error=str(error),
            )

        if outcome.exit_code != 0:
            return self._finish_stage(
                spec=spec,
                status="failed",
                started_at=started_at,
                started_monotonic=started_monotonic,
                exit_code=outcome.exit_code,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                artifacts=(),
                error=f"command exited with status {outcome.exit_code}",
            )

        try:
            artifacts = tuple(_artifact_record(path) for path in spec.required_outputs)
        except ValueError as error:
            return self._finish_stage(
                spec=spec,
                status="failed",
                started_at=started_at,
                started_monotonic=started_monotonic,
                exit_code=outcome.exit_code,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                artifacts=(),
                error=str(error),
            )

        return self._finish_stage(
            spec=spec,
            status="passed",
            started_at=started_at,
            started_monotonic=started_monotonic,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            artifacts=artifacts,
            error="",
        )

    def _finish_stage(
        self,
        *,
        spec: StageSpec,
        status: StageStatus,
        started_at: str,
        started_monotonic: float,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        artifacts: tuple[ArtifactRecord, ...],
        error: str,
    ) -> StageResult:
        log_dir = self._manifest_path.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / f"{spec.name}.stdout.log"
        stderr_log = log_dir / f"{spec.name}.stderr.log"
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")
        return StageResult(
            name=spec.name,
            status=status,
            fingerprint=spec.fingerprint,
            started_at_utc=started_at,
            completed_at_utc=_utc_now(),
            duration_ms=round((time.monotonic() - started_monotonic) * 1000),
            exit_code=exit_code,
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
            stdout_tail=stdout[-OUTPUT_TAIL_CHARACTERS:],
            stderr_tail=stderr[-OUTPUT_TAIL_CHARACTERS:],
            artifacts=artifacts,
            error=error,
        )

    def _reused_result(self, spec: StageSpec, prior: Mapping[str, Any]) -> StageResult:
        timestamp = _utc_now()
        artifacts = tuple(_artifact_record(path) for path in spec.required_outputs)
        return StageResult(
            name=spec.name,
            status="reused",
            fingerprint=spec.fingerprint,
            started_at_utc=timestamp,
            completed_at_utc=timestamp,
            duration_ms=0,
            exit_code=0,
            stdout_log=str(prior.get("stdout_log", "")),
            stderr_log=str(prior.get("stderr_log", "")),
            stdout_tail="",
            stderr_tail="",
            artifacts=artifacts,
            error="",
        )

    def _can_reuse(self, spec: StageSpec, prior: Mapping[str, Any]) -> bool:
        if prior.get("status") not in {"passed", "reused"}:
            return False
        if prior.get("fingerprint") != spec.fingerprint:
            return False
        raw_artifacts = prior.get("artifacts")
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(spec.required_outputs):
            return False
        try:
            current = tuple(_artifact_record(path) for path in spec.required_outputs)
        except ValueError:
            return False
        expected_hashes = tuple(
            value.get("sha256") if isinstance(value, dict) else None for value in raw_artifacts
        )
        return tuple(value.sha256 for value in current) == expected_hashes

    def _load_previous_stages(self) -> dict[str, Mapping[str, Any]]:
        if not self._manifest_path.is_file():
            return {}
        try:
            value: object = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid previous manifest {self._manifest_path}: {error}") from error
        if not isinstance(value, dict) or not isinstance(value.get("stages"), list):
            raise ValueError(f"Invalid previous manifest structure: {self._manifest_path}")
        return {
            stage["name"]: stage
            for stage in value["stages"]
            if isinstance(stage, dict) and isinstance(stage.get("name"), str)
        }

    def _build_result(
        self,
        *,
        run_id: str,
        status: RunStatus,
        started_at: str,
        started_monotonic: float,
        metadata: Mapping[str, Any],
        stages: Sequence[StageResult],
    ) -> PipelineResult:
        return PipelineResult(
            schema_version=1,
            run_id=run_id,
            status=status,
            started_at_utc=started_at,
            completed_at_utc=_utc_now(),
            duration_ms=round((time.monotonic() - started_monotonic) * 1000),
            cwd=str(self._cwd),
            metadata=dict(metadata),
            stages=tuple(stages),
        )

    def _write_manifest(self, result: PipelineResult) -> None:
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._manifest_path.with_suffix(self._manifest_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result.to_json_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._manifest_path)


def _artifact_record(path: Path) -> ArtifactRecord:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"missing required output: {resolved}")
    size = resolved.stat().st_size
    if size <= 0:
        raise ValueError(f"required output is empty: {resolved}")
    return ArtifactRecord(
        path=str(resolved),
        size_bytes=size,
        sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


def _decoded_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
