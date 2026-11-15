#!/usr/bin/env bash
# Launch GUI + terminal typing in this console (voice wake word still works)
# First run: creates .venv, installs deps, downloads models
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  BOOTSTRAP=python3
elif command -v python >/dev/null 2>&1; then
  BOOTSTRAP=python
else
  echo "[run] ERROR: Python 3.10+ is required." >&2
  exit 1
fi

"$BOOTSTRAP" setup_env.py

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" main.py --both "$@"
else
  echo "[run] ERROR: .venv not found after setup." >&2
  exit 1
fi
