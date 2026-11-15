#!/usr/bin/env bash
# Stop the voice assistant and unload Ollama models to free RAM
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
cd "$ROOT"

echo "[stop] Stopping Bayan / assistant (main.py)..."
# Match this project's main.py only (gui / both / terminal / no flags)
pkill -f "${ROOT}/main.py" 2>/dev/null || true
pkill -f "python3 .*main.py" 2>/dev/null || true
pkill -f "python .*main.py" 2>/dev/null || true
# Short wait so audio / GUI can exit cleanly
sleep 0.4
# Force if still alive
pkill -9 -f "${ROOT}/main.py" 2>/dev/null || true

echo "[stop] Unloading Ollama models from RAM..."
if command -v ollama >/dev/null 2>&1; then
  # Stop every currently loaded model
  if ollama ps >/dev/null 2>&1; then
    while read -r model; do
      [[ -z "${model:-}" || "$model" == "NAME" ]] && continue
      echo "  → ollama stop $model"
      ollama stop "$model" 2>/dev/null || true
    done < <(ollama ps 2>/dev/null | awk 'NR>1 {print $1}')
  fi
  # Drop leftover runner processes that still hold VRAM/RAM
  pkill -f "ollama runner" 2>/dev/null || true
else
  echo "  (ollama not found in PATH — skipped)"
fi

# Drop page cache hint (needs root; ignore if not allowed)
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  sync
  echo 3 >/proc/sys/vm/drop_caches 2>/dev/null || true
fi

echo "[stop] Done. Assistant stopped; Ollama models unloaded."
if command -v free >/dev/null 2>&1; then
  free -h | awk 'NR==1 || /^Mem:/'
fi
