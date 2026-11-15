# AI Developer Instructions - Voice & Vision Assistant Project

This file contains rules, structural guidelines, and technical context for AI coding assistants working on this repository. Follow these constraints when refactoring or writing new code.

---

## System Specs & Performance Constraints

- **Laptop Environment (Development):** 16GB RAM, CPU-only (no discrete GPU).
  - Use lightweight models: `qwen2.5:1.5b` or `qwen2.5:3b` for LLM tasks.
  - Use `moondream` (1.8B) for vision tasks.
  - Do NOT recommend large models (>7B parameters) for local inference on this hardware.
  - Prefer `num_thread: 2` on weak CPUs; raise on stronger machines.
- **Home PC Environment (Deployment Target):** RTX 3060 Ti (8GB VRAM).
  - Can host `qwen2.5-coder:7b` or `llama3.1:8b` via Ollama.
  - Optimize memory usage so that VRAM stays under 8GB when running voice + vision + code assistants simultaneously.

---

## Codebase Architecture

1. **`main.py` (Main Entry Point & Coordination):**
   - Coordinates openWakeWord + Faster-Whisper (or Vosk fallback), PySide6 GUI / terminal, Ollama pipelines, TTS worker.
   - Decoupled reasoning dispatcher: `process_and_respond(command)`.
   - Run flags: `--terminal` / `-t`, `--gui` / `-g`, `--consolidate`.
2. **`memory_manager.py` (ChromaDB):** Semantic memory + Strategy C compaction. Lazy-loaded after voice ready.
3. **`vision_engine.py`:** Wayland screenshot + webcam → vision LLM.
4. **`scheduler.py`:** Reminders, timers, alarms (EN + Arabic phrases).
5. **`tools.py`:** Volume, media, apps, status, lock, time/date, jokes.
6. **`config.json`:** All runtime settings. No hardcoded environment choices.

---

## Configuration Schema (`config.json`)

```json
{
    "mode": "gui",
    "language": "auto",
    "llm_model": "gemma3:4b",
    "vision_model": "moondream",
    "tts_engine": "piper",
    "arabic_tts_engine": "edge-tts",
    "voice_name": "en_US-lessac-medium",
    "arabic_voice_name": "ar-SA-ZariyahNeural",
    "piper_arabic_fallback": "ar_JO-kareem-medium",
    "piper_voices_dir": "./piper_voices",
    "voice_speed": 1.1,
    "sarcastic_personality": false,
    "enable_reasoning": true,
    "num_thread": 4,
    "num_ctx": 4096,
    "enable_memory": true,
    "memory_db_path": "./memory_db",
    "memory_summarization_threshold": 15,
    "stt_engine": "whisper",
    "wake_word_engine": "openwakeword",
    "wake_word_model": "alexa",
    "wake_word_threshold": 0.55,
    "wake_word_cooldown_sec": 2.0,
    "wake_min_consecutive": 2,
    "whisper_model": "tiny.en",
    "whisper_device": "cpu",
    "vad_threshold": 0.20,
    "enable_reminders": true,
    "enable_tools": true,
    "enable_idle_chatter": true,
    "idle_chatter_min_sec": 90,
    "idle_chatter_max_sec": 180
}
```

### Language / TTS

- `"language": "auto"` — detect from each user utterance (Arabic script / Whisper); reply language matches.
- `"language": "en"` / `"ar"` — lock replies to that language.
- Hybrid default: Piper EN (female, offline) + Edge-TTS Arabic female (`ar-SA-ZariyahNeural`, needs Wi‑Fi).
- `"piper_arabic_fallback"` — male Piper AR used only if Edge is offline.
- `"enable_reasoning": true` — model may emit private `<think>...</think>`; TTS/UI strip it.
- Prefer `gemma3:4b` (or similar ≥3B) over tiny 1.5B for college-demo quality.
- Wake word: openWakeWord (`bayan` custom / `alexa` / `hey_jarvis`).

### Demo tips

- Say **Alexa**, then: set a timer / alarm, ask time, search weather/news, remember a note, ask a joke.
- Idle chatter speaks occasionally when quiet (`enable_idle_chatter`).
- Esc or SIGUSR1 = kill speech mid-reply.

---

## Code Conventions & Rules

1. **Keep Latency Low:** Stream LLM (`stream=True`); flush sentences on punctuation into `tts_queue`.
2. **Anti-echo:** Never unmute mic while `AudioStreamPlayer` estimates playback remaining or during `wake_blocked_until` cooldown. Discard STT that overlaps the last AI reply.
3. **Safe Resource Handling:** Close webcam/audio in try/finally; wrap sounddevice failures.
4. **Async Patterns:** Mic loop, TTS worker, LLM stream, GUI on separate threads; coordinate via queues and flags.
5. **Phrase cache:** Prefer `.voice_cache/` disk cache + small in-memory phrase sets for fast startup.
