#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
EXPAT_LIB="/opt/homebrew/opt/expat/lib"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Missing project venv Python: ${VENV_PYTHON}" >&2
  echo "Create it with: python3 -m venv .venv; then install requirements-dev.txt" >&2
  exit 2
fi

if [[ -d "${EXPAT_LIB}" ]]; then
  export DYLD_LIBRARY_PATH="${EXPAT_LIB}:${DYLD_LIBRARY_PATH:-}"
fi

cd "${PROJECT_ROOT}"

for ENV_FILE in "${PROJECT_ROOT}/.env" "${PROJECT_ROOT}/.env.local"; do
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
  fi
done

exec "${VENV_PYTHON}" "$@"
