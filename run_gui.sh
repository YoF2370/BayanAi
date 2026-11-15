#!/usr/bin/env bash
# Launch assistant GUI only (voice + on-screen chat box)
# First run: creates .venv, installs deps, downloads models
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
cd "$ROOT"

# Prefer system python3 for bootstrap (creates .venv)
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
  exec "$ROOT/.venv/bin/python" main.py --gui "$@"
else
  echo "[run] ERROR: .venv not found after setup." >&2
  exit 1
fi
