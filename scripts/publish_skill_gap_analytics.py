#!/usr/bin/env python3
"""Export strict workflow reviews and publish them to jobtracker analytics.

The workflow review JSON remains the source of truth. This stage emits the
jobtracker contract-v1 artifact before attempting the authenticated import, so
an unavailable tracker never loses the day's normalized observations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import unicodedata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

if __package__:
    from scripts.mark_applied_jobs import extract_requisition_id
else:
    from mark_applied_jobs import extract_requisition_id


DEFAULT_TRACKER_ENDPOINT = "http://127.0.0.1:8086/api/analytics/workflow-runs"
TOKEN_ENVIRONMENT_VARIABLE = "JOBTRACKER_ANALYTICS_IMPORT_TOKEN"
ENDPOINT_ENVIRONMENT_VARIABLE = "JOBTRACKER_ANALYTICS_URL"
LOCATION_MAXIMUM = 300

SOURCE_DECISIONS = {
    "strong apply": "strong_apply",
    "apply": "apply",
    "maybe": "moderate_stretch",
    "moderate stretch": "moderate_stretch",
    "skip": "do_not_apply",
    "do not apply": "do_not_apply",
}
STRICT_DECISIONS = {
    "strong apply": "strong_apply",
    "apply": "apply",
    "moderate stretch": "moderate_stretch",
    "extreme stretch": "do_not_apply",
    "skip": "do_not_apply",
    "do not apply": "do_not_apply",
}
RESOLUTION_STATES = {
    "professional_evidence",
    "limited_exposure",
    "do_not_claim",
    "posting_false_positive",
}
INACTIVE_RESOLUTION_STATES = {"professional_evidence", "posting_false_positive"}
SKILL_ALIASES = {
    "typescript professional experience": ("typescript", "TypeScript"),
    "python backend professional experience": ("python", "Python"),
    "mcp servers": ("mcp", "MCP"),
    "salesforce apex": ("salesforce_apex", "Salesforce Apex"),
    "ruby on rails": ("ruby_on_rails", "Ruby on Rails"),
    "double-entry accounting": ("double-entry_accounting", "Double-entry accounting"),
}

REMOTE_PUBLISH_SCRIPT = r"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

endpoint = sys.argv[1]
timeout_seconds = int(sys.argv[2])
kubeconfig = str(Path.home() / ".kube" / "config")
secret_result = subprocess.run(
    [
        "kubectl", "--kubeconfig", kubeconfig, "-n", "jobtracker", "get", "secret",
        "jobtracker-analytics-import", "-o", "json",
    ],
    check=False,
    capture_output=True,
    text=True,
)
if secret_result.returncode != 0:
    raise SystemExit("jobtracker analytics secret is missing or unreadable: " + secret_result.stderr.strip())
secret = json.loads(secret_result.stdout)
encoded_token = (secret.get("data") or {}).get("token")
if not isinstance(encoded_token, str) or not encoded_token:
    raise SystemExit("jobtracker analytics secret does not contain token")
token = base64.b64decode(encoded_token, validate=True).decode("utf-8")
payload = sys.stdin.buffer.read()
if not payload:
    raise SystemExit("analytics payload is empty")
request = Request(
    endpoint,
    data=payload,
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    method="POST",
)
try:
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
except HTTPError as error:
    detail = error.read().decode("utf-8", errors="replace")[:500]
    raise SystemExit(f"jobtracker import returned HTTP {error.code}: {detail}")
except URLError as error:
    raise SystemExit(f"unable to reach local jobtracker endpoint: {error.reason}")
sys.stdout.buffer.write(body)
""".strip()


class AnalyticsPublishError(RuntimeError):
    """Raised when jobtracker does not safely accept an analytics payload."""


def normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def canonical_skill_key(raw_label: str) -> str:
    """Return a deterministic tracker-safe key for a reviewer skill label."""
    label = normalized_text(raw_label)
    if not label:
        raise ValueError("Cannot canonicalize an empty skill label")
    if label in SKILL_ALIASES:
        return SKILL_ALIASES[label][0]
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    key = re.sub(r"[^a-z0-9._+\-]+", "_", ascii_label).strip("_.-")
    if not key:
        raise ValueError(f"Skill label {raw_label!r} has no canonical characters")
    if len(key) > 80:
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
        key = f"{key[:67].rstrip('_.-')}-{digest}"
    if re.fullmatch(r"[a-z0-9][a-z0-9._+\-]{0,79}", key) is None:
        raise ValueError(f"Canonical skill key {key!r} is invalid for {raw_label!r}")
    return key


def display_name_for_skill(raw_label: str) -> str:
    alias = SKILL_ALIASES.get(normalized_text(raw_label))
    return alias[1] if alias else raw_label.strip()


def map_decision(value: object, *, source: bool) -> str:
    normalized = normalized_text(value)
    mapping = SOURCE_DECISIONS if source else STRICT_DECISIONS
    if normalized not in mapping:
        label = "source" if source else "strict workflow"
        raise ValueError(f"Unsupported {label} decision: {value!r}")
    return mapping[normalized]


def map_work_model(value: object) -> str:
    normalized = normalized_text(value)
    if "not stated" in normalized or "not specified" in normalized:
        return "unspecified"
    if "hybrid" in normalized:
        return "hybrid"
    if "remote" in normalized or "virtual" in normalized:
        return "remote"
    if "onsite" in normalized or "on-site" in normalized or "on site" in normalized:
        return "onsite"
    if normalized in {"", "unknown", "unspecified", "flex"}:
        return "unspecified"
    raise ValueError(f"Unsupported work model: {value!r}")


def canonical_url(value: object) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Job URL must be an absolute HTTP(S) URL: {raw!r}")
    canonical = urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, parsed.query, ""))
    if len(canonical) > 2048:
        raise ValueError(f"Job URL exceeds 2048 characters: {raw!r}")
    return canonical


def parse_resolution(answer: object) -> tuple[str, str | None]:
    if answer is None or not str(answer).strip():
        return "unanswered", None
    raw = str(answer).strip()
    state_part, separator, evidence_part = raw.partition(":")
    state = normalized_text(state_part).replace(" ", "_")
    if state not in RESOLUTION_STATES:
        raise ValueError(f"Unsupported gap resolution: {answer!r}")
    evidence = evidence_part.strip() if separator and evidence_part.strip() else None
    if evidence is not None and len(evidence) > 1000:
        raise ValueError("Gap resolution evidence exceeds 1000 characters")
    return state, evidence


def primary_blocker_for(review: dict[str, Any], *, has_gaps: bool) -> str:
    evidence = " ".join(
        str(item) for item in [*(review.get("hard_blockers") or []), *(review.get("risk_flags") or [])]
    ).casefold()
    if "clearance" in evidence:
        return "clearance"
    if any(marker in evidence for marker in ("location", "west coast", "international-only", "onsite")):
        return "location"
    if any(marker in evidence for marker in ("authorization", "visa", "sponsorship")):
        return "employment_authorization"
    if "compensation" in evidence or "salary" in evidence:
        return "compensation"
    if any(marker in evidence for marker in ("primary stack", "low-fit skip", "sales-only", "unrelated")):
        return "unrelated"
    return "skill_gap" if has_gaps else "none"


def _required_string(review: dict[str, Any], field: str, *, maximum: int) -> str:
    value = review.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Workflow review field {field!r} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise ValueError(f"Workflow review field {field!r} exceeds {maximum} characters")
    return result


def build_gap_records(review: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = review.get("resolved_gaps") or {}
    if not isinstance(resolved, dict):
        raise ValueError("Workflow review resolved_gaps must be an object")

    labels: list[tuple[str, str]] = []
    for classification, field in (("required", "required_gaps"), ("preferred", "preferred_gaps")):
        raw_values = review.get(field) or []
        if not isinstance(raw_values, list) or any(not isinstance(item, str) for item in raw_values):
            raise ValueError(f"Workflow review {field} must be a list of strings")
        labels.extend((item, classification) for item in raw_values)

    records_by_key: dict[str, dict[str, Any]] = {}
    for raw_label, classification in labels:
        key = canonical_skill_key(raw_label)
        state, evidence = parse_resolution(resolved.get(raw_label))
        record = {
            "skill_key": key,
            "display_name": display_name_for_skill(raw_label),
            "raw_posting_label": raw_label.strip(),
            "classification": classification,
            "hard_blocker": classification == "required",
            "resolution_state": state,
            "resolution_evidence": evidence,
            "active": state not in INACTIVE_RESOLUTION_STATES,
        }
        previous = records_by_key.get(key)
        if previous is None or classification == "required":
            records_by_key[key] = record
    return sorted(records_by_key.values(), key=lambda item: (item["classification"] != "required", item["skill_key"]))


def build_posting(review: dict[str, Any]) -> dict[str, Any]:
    company = _required_string(review, "company", maximum=200)
    title = _required_string(review, "title", maximum=240)
    url = canonical_url(review.get("url"))
    requisition_id = extract_requisition_id(url) or None
    posting_key = (
        f"req:{requisition_id.casefold()}"
        if requisition_id
        else f"url:{hashlib.sha256(url.encode('utf-8')).hexdigest()}"
    )
    location_value = str(review.get("location") or "").strip() or None
    if location_value is not None and len(location_value) > LOCATION_MAXIMUM:
        location_value = location_value[: LOCATION_MAXIMUM - 3].rstrip(" ;,") + "..."
    try:
        fit_score = float(review.get("source_fit_score"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid workflow fit score: {review.get('source_fit_score')!r}") from error
    if not 0 <= fit_score <= 100:
        raise ValueError(f"Workflow fit score must be from 0 to 100: {fit_score}")
    gaps = build_gap_records(review)
    return {
        "posting_key": posting_key,
        "requisition_id": requisition_id,
        "canonical_url": url,
        "company": company,
        "title": title,
        "location": location_value,
        "work_model": map_work_model(review.get("work_model")),
        "source_decision": map_decision(review.get("source_decision"), source=True),
        "fit_score": fit_score,
        "strict_workflow_decision": map_decision(review.get("workflow_decision"), source=False),
        "primary_blocker": primary_blocker_for(review, has_gaps=bool(gaps)),
        "already_applied": bool(review.get("already_applied", False)),
        "gaps": gaps,
    }


def validate_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Workflow run date must be a string: {value!r}")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Workflow run date must be YYYY-MM-DD: {value!r}") from error
    if parsed.isoformat() != value:
        raise ValueError(f"Workflow run date must be canonical YYYY-MM-DD: {value!r}")
    return value


def dedupe_postings(postings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first current-review record for each exact posting identity."""
    seen_requisition_ids: set[str] = set()
    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for posting in postings:
        requisition_id = str(posting.get("requisition_id") or "").strip().casefold()
        url = str(posting.get("canonical_url") or "").strip().casefold()
        if requisition_id and requisition_id in seen_requisition_ids:
            continue
        if url and url in seen_urls:
            continue
        deduped.append(posting)
        if requisition_id:
            seen_requisition_ids.add(requisition_id)
        if url:
            seen_urls.add(url)
    return deduped


def build_import_payload(
    *,
    review: dict[str, Any],
    source_artifact_name: str,
    source_bytes: bytes,
    workflow_profile_bytes: bytes,
    expected_date: str,
) -> dict[str, Any]:
    run_date = validate_date(review.get("generated_on"))
    if run_date != expected_date:
        raise ValueError(f"Workflow review date {run_date} does not match requested date {expected_date}")
    raw_reviews = review.get("reviews")
    if not isinstance(raw_reviews, list) or any(not isinstance(item, dict) for item in raw_reviews):
        raise ValueError("Workflow review must contain a reviews list of objects")
    if len(raw_reviews) > 500:
        raise ValueError(f"Workflow review has {len(raw_reviews)} postings; tracker limit is 500")
    postings = dedupe_postings([build_posting(item) for item in raw_reviews])
    postings.sort(key=lambda item: (item["company"].casefold(), item["title"].casefold(), item["posting_key"]))
    gap_count = sum(len(item["gaps"]) for item in postings)
    if gap_count > 5000:
        raise ValueError(f"Workflow review has {gap_count} gap observations; tracker limit is 5000")
    if not source_artifact_name or len(source_artifact_name) > 255:
        raise ValueError("Source artifact name must contain 1 to 255 characters")
    return {
        "schema_version": 1,
        "run_date": run_date,
        "source_artifact_name": source_artifact_name,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "workflow_profile_version": f"sha256:{hashlib.sha256(workflow_profile_bytes).hexdigest()}",
        "postings": postings,
        "training_statuses": [],
    }


def write_import_payload(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def publish_payload(
    *, endpoint: str, token: str, payload: dict[str, Any], timeout_seconds: int
) -> dict[str, Any]:
    if not token.strip():
        raise AnalyticsPublishError(
            f"Missing {TOKEN_ENVIRONMENT_VARIABLE}; analytics import is not authenticated"
        )
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    request = Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise AnalyticsPublishError(f"Jobtracker import returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise AnalyticsPublishError(f"Unable to reach jobtracker analytics endpoint: {error.reason}") from error
    if not 200 <= status < 300:
        raise AnalyticsPublishError(f"Jobtracker import returned unexpected HTTP status {status}")
    try:
        decoded: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalyticsPublishError("Jobtracker import returned invalid JSON") from error
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        raise AnalyticsPublishError("Jobtracker import did not confirm success")
    return decoded


def publish_payload_via_ssh(
    *, ssh_host: str, endpoint: str, payload: dict[str, Any], timeout_seconds: int
) -> dict[str, Any]:
    """Publish through an SSH host while keeping the Kubernetes Bearer token remote."""
    if not ssh_host.strip():
        raise ValueError("ssh_host must be non-empty")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    remote_command = " ".join(
        (
            "python3 -c",
            shlex.quote(REMOTE_PUBLISH_SCRIPT),
            shlex.quote(endpoint),
            str(timeout_seconds),
        )
    )
    try:
        result = subprocess.run(
            ["ssh", ssh_host, remote_command],
            check=False,
            capture_output=True,
            input=serialized,
            text=True,
            timeout=timeout_seconds + 20,
        )
    except subprocess.TimeoutExpired as error:
        raise AnalyticsPublishError(
            f"SSH analytics import timed out after {timeout_seconds + 20} seconds"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip()[:500] or "remote command returned no diagnostic"
        raise AnalyticsPublishError(f"SSH analytics import failed: {detail}")
    try:
        decoded: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AnalyticsPublishError("SSH analytics import returned invalid JSON") from error
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        raise AnalyticsPublishError("SSH analytics import did not confirm success")
    return decoded


def load_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    source_bytes = path.read_bytes()
    decoded: object = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return decoded, source_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-review", type=Path, required=True)
    parser.add_argument("--workflow-profile", type=Path, default=Path("candidate_workflow_profile.yml"))
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--tracker-url",
        default=os.environ.get(ENDPOINT_ENVIRONMENT_VARIABLE, DEFAULT_TRACKER_ENDPOINT),
    )
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--ssh-host",
        help="Publish through this host using its Kubernetes secret; no local token is read.",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Write the contract artifact without POSTing it (for diagnostics or credential setup).",
    )
    args = parser.parse_args()

    expected_date = validate_date(args.date)
    review, source_bytes = load_json_object(args.workflow_review)
    workflow_profile_bytes = args.workflow_profile.read_bytes()
    payload = build_import_payload(
        review=review,
        source_artifact_name=args.workflow_review.name,
        source_bytes=source_bytes,
        workflow_profile_bytes=workflow_profile_bytes,
        expected_date=expected_date,
    )
    artifact_path = args.out / f"{expected_date}-skill-gap-observations.json"
    write_import_payload(artifact_path, payload)
    print(f"Wrote {artifact_path} ({len(payload['postings'])} postings)")
    if args.export_only:
        print("Export-only mode: jobtracker import was not attempted")
        return 0

    if args.ssh_host:
        response = publish_payload_via_ssh(
            ssh_host=args.ssh_host,
            endpoint=args.tracker_url,
            payload=payload,
            timeout_seconds=args.timeout_seconds,
        )
    else:
        response = publish_payload(
            endpoint=args.tracker_url,
            token=os.environ.get(TOKEN_ENVIRONMENT_VARIABLE, ""),
            payload=payload,
            timeout_seconds=args.timeout_seconds,
        )
    result_path = args.out / f"{expected_date}-skill-gap-import-result.json"
    result = {
        "artifact": str(artifact_path),
        "endpoint": args.tracker_url,
        "imported_at": dt.datetime.now(dt.UTC).isoformat(),
        "response": response,
    }
    write_import_payload(result_path, result)
    print(f"Imported analytics run and wrote {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
