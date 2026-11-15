#!/usr/bin/env python3
"""
Cross-platform first-run bootstrap for BayanAi.

Creates .venv, installs requirements, downloads Piper voices + Vosk model,
checks/pulls Ollama models. Idempotent — safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
SETUP_MARKER = ROOT / ".setup_complete"
SETUP_VERSION = "1"

PIPER_VOICES_DIR = ROOT / "piper_voices"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
PIPER_VOICES = [
    {
        "name": "en_US-lessac-medium",
        "onnx": f"{PIPER_BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "json": f"{PIPER_BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    },
    {
        "name": "ar_JO-kareem-medium",
        "onnx": f"{PIPER_BASE}/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx",
        "json": f"{PIPER_BASE}/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json",
    },
]

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_DIR = ROOT / "model"

KOKORO_ONNX = ROOT / "kokoro-v1.0.onnx"
KOKORO_VOICES = ROOT / "voices-v1.0.bin"
# Official kokoro-onnx release assets
KOKORO_ONNX_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)

OLLAMA_MODELS = ("qwen2.5:3b", "moondream")


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_pip() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def ensure_venv() -> Path:
    py = venv_python()
    if py.is_file():
        log(f"Using existing venv: {VENV_DIR}")
        return py

    log(f"Creating virtual environment at {VENV_DIR} ...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not py.is_file():
        raise RuntimeError(f"venv created but interpreter missing: {py}")
    return py


def pip_install(py: Path) -> None:
    if not REQUIREMENTS.is_file():
        raise FileNotFoundError(f"Missing {REQUIREMENTS}")

    log("Upgrading pip ...")
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"])

    log(f"Installing dependencies from {REQUIREMENTS.name} ...")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    )


def download_file(url: str, dest: Path, label: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    name = label or dest.name
    if dest.is_file() and dest.stat().st_size > 0:
        log(f"Already present: {name}")
        return

    log(f"Downloading {name} ...")
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BayanAi-setup/1.0"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
            total = resp.headers.get("Content-Length")
            total_i = int(total) if total and total.isdigit() else None
            done = 0
            chunk = 1024 * 256
            last_pct = -1
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                out.write(data)
                done += len(data)
                if total_i:
                    pct = int(done * 100 / total_i)
                    if pct >= last_pct + 10:
                        log(f"  {name}: {pct}%")
                        last_pct = pct
        tmp.replace(dest)
        log(f"Saved {name} ({dest.stat().st_size // (1024 * 1024)} MB)")
    except Exception:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        raise


def download_piper_voices() -> None:
    PIPER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    for voice in PIPER_VOICES:
        onnx_path = PIPER_VOICES_DIR / f"{voice['name']}.onnx"
        json_path = PIPER_VOICES_DIR / f"{voice['name']}.onnx.json"
        download_file(voice["onnx"], onnx_path, f"{voice['name']}.onnx")
        download_file(voice["json"], json_path, f"{voice['name']}.onnx.json")


def download_vosk_model() -> None:
    # Valid Vosk tree has am/ and graph/ under model/
    if (VOSK_DIR / "am").is_dir() and (VOSK_DIR / "graph").is_dir():
        log("Vosk model already present at model/")
        return

    log("Downloading Vosk English model (fallback STT) ...")
    with tempfile.TemporaryDirectory(prefix="bayan_vosk_") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "vosk.zip"
        download_file(VOSK_URL, zip_path, "vosk-model-small-en-us-0.15.zip")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_path)

        extracted = None
        for child in tmp_path.iterdir():
            if child.is_dir() and child.name.startswith("vosk-model"):
                extracted = child
                break
        if extracted is None:
            raise RuntimeError("Vosk zip extracted but model folder not found")

        if VOSK_DIR.exists():
            shutil.rmtree(VOSK_DIR)
        shutil.move(str(extracted), str(VOSK_DIR))
    log("Vosk model ready at model/")


def download_kokoro() -> None:
    download_file(KOKORO_ONNX_URL, KOKORO_ONNX, "kokoro-v1.0.onnx")
    download_file(KOKORO_VOICES_URL, KOKORO_VOICES, "voices-v1.0.bin")


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def ensure_ollama() -> None:
    ollama = which("ollama")
    if not ollama:
        log("Ollama not found on PATH.")
        log("  Install from https://ollama.com then re-run setup, or pull models manually:")
        for m in OLLAMA_MODELS:
            log(f"    ollama pull {m}")
        return

    log(f"Found Ollama: {ollama}")
    # Ensure daemon can be reached; ignore failures (user may start later)
    try:
        subprocess.run(
            [ollama, "list"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        log(f"Could not query Ollama ({e}). Start the Ollama app/service before running Bayan.")
        return

    for model in OLLAMA_MODELS:
        log(f"Pulling Ollama model '{model}' (skips if already present) ...")
        try:
            subprocess.check_call([ollama, "pull", model])
        except subprocess.CalledProcessError as e:
            log(f"Warning: failed to pull {model}: {e}")


def ensure_config() -> None:
    config = ROOT / "config.json"
    example = ROOT / "config.example.json"
    if not config.is_file() and example.is_file():
        log("Creating config.json from config.example.json")
        shutil.copy(example, config)


def write_marker() -> None:
    payload = {
        "version": SETUP_VERSION,
        "python": str(venv_python()),
        "platform": platform.platform(),
    }
    SETUP_MARKER.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def marker_ok() -> bool:
    if not SETUP_MARKER.is_file():
        return False
    try:
        data = json.loads(SETUP_MARKER.read_text(encoding="utf-8"))
        return data.get("version") == SETUP_VERSION and venv_python().is_file()
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BayanAi environment setup")
    p.add_argument(
        "--with-kokoro",
        action="store_true",
        help="Also download Kokoro TTS model files (~338 MB)",
    )
    p.add_argument(
        "--skip-ollama",
        action="store_true",
        help="Skip Ollama presence check and model pulls",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run pip install even if setup marker exists",
    )
    return p.parse_args()


def main() -> int:
    os.chdir(ROOT)
    args = parse_args()

    log(f"Project root: {ROOT}")
    log(f"Platform: {platform.system()} {platform.machine()}")

    if sys.version_info < (3, 10):
        log(f"ERROR: Python 3.10+ required (found {sys.version})")
        return 1

    ensure_config()
    py = ensure_venv()

    need_pip = args.force or not marker_ok()
    if need_pip:
        pip_install(py)
    else:
        log("Dependencies already installed (use --force to reinstall)")

    download_piper_voices()
    download_vosk_model()

    if args.with_kokoro:
        download_kokoro()
    else:
        if not KOKORO_ONNX.is_file():
            log("Skipping Kokoro download (default TTS is Piper). Use --with-kokoro if needed.")

    if not args.skip_ollama:
        ensure_ollama()

    write_marker()
    log("Setup complete.")
    log(f"Launch with: {venv_python()} main.py --gui")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[setup] Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"[setup] ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
