# BayanAi

Local voice-first desktop assistant with English and Arabic support. Wake-word listening, Faster-Whisper speech-to-text, Ollama for reasoning/vision, Piper + Edge-TTS for speech, and an optional PySide6 floating GUI.

## Prerequisites

- **Python 3.10+** ([python.org](https://www.python.org/downloads/) — on Windows, enable “Add python.exe to PATH”)
- **Microphone** (and speakers)
- **[Ollama](https://ollama.com)** installed and running (LLM + vision)

First launch will create a virtualenv, install Python packages, download Piper voices and a Vosk fallback model, and pull Ollama models `qwen2.5:3b` and `moondream` when Ollama is available.

## Quick start

Clone this repo, then use the launcher for your OS (no manual `pip install` needed):

| OS | GUI only | GUI + terminal chat |
|----|----------|---------------------|
| **Linux / macOS** | `./run_gui.sh` | `./run_terminal_gui.sh` |
| **macOS (Finder)** | Double-click `run_gui.command` | — |
| **Windows** | Double-click `run_gui.bat` | Double-click `run_terminal_gui.bat` |

First run can take several minutes (pip + model downloads). Later runs reuse `.venv` and cached assets.

### Manual / advanced

```bash
python3 setup_env.py          # or: py -3 setup_env.py on Windows
.venv/bin/python main.py --gui
# Windows: .venv\Scripts\python.exe main.py --gui
```

Useful flags:

- `--gui` / `-g` — floating window
- `--both` / `-b` — GUI + type in the terminal
- `--terminal` / `-t` — terminal only
- `--consolidate` — compact memory DB only

Optional setup flags:

```bash
python3 setup_env.py --with-kokoro   # download Kokoro TTS (~338 MB)
python3 setup_env.py --skip-ollama   # skip Ollama checks/pulls
python3 setup_env.py --force         # reinstall pip packages
```

### Stop

- **Linux / macOS:** `./stop.sh`
- **Windows:** `stop.bat`

Stops Bayan processes and unloads loaded Ollama models from RAM.

## Configuration

Copy or edit [`config.json`](config.json) (see [`config.example.json`](config.example.json) for defaults). Important keys:

| Key | Default | Notes |
|-----|---------|--------|
| `wake_word_model` | `bayan` | Loads `./bayan.onnx`; use a pretrained openWakeWord name if the file is missing |
| `tts_engine` | `piper` | Offline EN; set `kokoro` only after `--with-kokoro` |
| `arabic_tts_engine` | `edge-tts` | Neural Arabic (needs network); Piper fallback offline |
| `llm_model` / `vision_model` | `qwen2.5:3b` / `moondream` | Must exist in Ollama |
| `stt_engine` | `whisper` | Faster-Whisper; Vosk used as fallback |

## Platform notes

| Feature | Linux | Windows | macOS |
|---------|-------|---------|-------|
| Voice wake + STT + TTS + GUI | Yes | Yes | Yes |
| Ollama chat / webcam vision | Yes | Yes | Yes |
| Screen vision (GNOME Wayland DBus) | Yes (GNOME + PyGObject) | No | No |
| Desktop tools (volume, lock, apps, …) | Yes | Limited / Linux-oriented | Limited / Linux-oriented |

On Linux, screen capture optionally needs system **PyGObject** (`python3-gi` / similar). It is not in `requirements.txt` so Windows/macOS installs stay clean.

Arabic Edge-TTS needs internet. Piper voices work offline once downloaded by setup.

## Project layout

- `main.py` — orchestrator (wake, STT, TTS, LLM, GUI)
- `memory_manager.py` — ChromaDB long-term memory
- `scheduler.py` — reminders / timers
- `tools.py` — local desktop commands (Linux-oriented)
- `vision_engine.py` — screen / webcam + Ollama vision
- `setup_env.py` — cross-platform bootstrap
- `bayan.onnx` — custom wake word
- `train_wakeword.ipynb` — Colab notebook to retrain the wake word
- `audio_engine.py` — legacy prototype (not used by `main.py`)

Large models (`piper_voices/*.onnx`, Vosk `model/`, optional Kokoro) are **not** stored in git; `setup_env.py` downloads them.

## License

Project code is **MIT** — see [LICENSE](LICENSE).

Third-party assets and models have their own terms, including:

- [Vosk](https://alphacephei.com/vosk/) acoustic models
- [Piper](https://github.com/rhasspy/piper) voices (Hugging Face `rhasspy/piper-voices`)
- [Edge-TTS](https://github.com/rany2/edge-tts) / Microsoft neural voices (network service)
- [openWakeWord](https://github.com/dscripka/openWakeWord), Faster-Whisper, Ollama models
- Optional [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) model files
- GUI GIF animations may be third-party; replace them if your redistribution needs clearer licensing

## Troubleshooting

- **“Ollama not found”** — install from https://ollama.com, start the app/service, then re-run the launcher (or `ollama pull qwen2.5:3b` and `ollama pull moondream`).
- **No microphone** — allow mic access in OS privacy settings; PortAudio backends vary by platform (`sounddevice`).
- **First Whisper load is slow** — Faster-Whisper downloads the `tiny` model into its cache once.
- **Wake word not firing** — ensure `bayan.onnx` is in the project root, or set `wake_word_model` to a pretrained name (e.g. `alexa`, `hey_jarvis`).
