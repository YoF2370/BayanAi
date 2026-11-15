import sys
import os
import random
import threading
import queue
import json
import signal
import time
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from ddgs import DDGS

# Audio & AI Imports
import sounddevice as sd
import ollama
# vosk / kokoro_onnx are lazy-imported when those engines are selected

# Vision Imports (gi/PyGObject is lazy inside vision_engine — safe on Windows/macOS)
from vision_engine import capture_wayland_screen, capture_hardware_webcam, analyze_visual_input

# GUI Imports
from PySide6.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QTextEdit, QLineEdit
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QMovie

# --- 1. CONFIGURATION SYSTEM ---
CONFIG = {
    "mode": "gui",
    "language": "auto",
    "llm_model": "qwen2.5:3b",
    "vision_model": "moondream",
    "tts_engine": "piper",
    "arabic_tts_engine": "edge-tts",
    "voice_name": "en_US-lessac-medium",
    "arabic_voice_name": "ar-SA-ZariyahNeural",
    "piper_arabic_fallback": "ar_JO-kareem-medium",
    "piper_voices_dir": "./piper_voices",
    "voice_speed": 1.05,
    "arabic_voice_speed": 0.95,
    "sarcastic_personality": False,
    "enable_reasoning": False,
    "enable_thinking_fillers": False,
    "num_predict": 120,
    "num_thread": 2,
    "num_ctx": 2048,
    "enable_memory": True,
    "memory_db_path": "./memory_db",
    "memory_summarization_threshold": 15,
    "stt_engine": "whisper",
    "wake_word_engine": "openwakeword",
    "wake_word_model": "alexa",
    "wake_word_threshold": 0.55,
    "wake_word_cooldown_sec": 2.0,
    "wake_min_consecutive": 2,
    "whisper_model": "tiny",
    "whisper_device": "cpu",
    "vad_threshold": 0.20,
    "enable_reminders": True,
    "enable_tools": True,
    "enable_idle_chatter": True,
    "idle_chatter_min_sec": 90,
    "idle_chatter_max_sec": 180,
}

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
if os.path.exists(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            CONFIG.update(json.load(f))
        print(f"[CONFIG] Loaded config from {config_path}")
    except Exception as e:
        print(f"[CONFIG Warning] Error loading config.json: {e}")

if "--both" in sys.argv or "-b" in sys.argv:
    CONFIG["mode"] = "both"  # GUI window + terminal stdin
elif "--terminal" in sys.argv or "-t" in sys.argv:
    CONFIG["mode"] = "terminal"
elif "--gui" in sys.argv or "-g" in sys.argv:
    CONFIG["mode"] = "gui"


# Per-turn reply language: "en" | "ar" (updated when user speaks)
current_reply_lang = "en"


def arabic_char_ratio(text):
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha() or ("\u0600" <= c <= "\u06FF")]
    if not letters:
        return 0.0
    ar = sum(1 for c in letters if "\u0600" <= c <= "\u06FF")
    return ar / float(len(letters))


def detect_language_from_text(text, whisper_lang=None):
    """Detect reply language from transcript (+ optional Whisper lang code)."""
    cfg = str(CONFIG.get("language", "auto")).lower()
    if cfg in ("en", "english"):
        return "en"
    if cfg in ("ar", "arabic", "ar_iq", "ar-iq", "iraqi"):
        return "ar"

    # auto mode
    if arabic_char_ratio(text) >= 0.25:
        return "ar"
    if whisper_lang and str(whisper_lang).lower().startswith("ar"):
        return "ar"
    return "en"


def is_arabic_language(lang=None):
    code = (lang or current_reply_lang or CONFIG.get("language", "en"))
    code = str(code).lower()
    if code == "auto":
        code = current_reply_lang
    return str(code).startswith("ar")


def resolve_tts_engine():
    """Primary TTS engine for English / default path."""
    engine = CONFIG.get("tts_engine", "piper")
    if engine == "auto":
        return "piper"
    return engine


def resolve_arabic_tts_engine():
    """Arabic voice: Edge-TTS female by default (Piper has no female Arabic voice)."""
    return CONFIG.get("arabic_tts_engine", "edge-tts")


def resolve_voice_for_text(text):
    """Pick voice id from sentence script / session language."""
    want_ar = arabic_char_ratio(text) >= 0.2 or (not (text or "").strip() and is_arabic_language())
    if want_ar:
        return CONFIG.get("arabic_voice_name", "ar-SA-ZariyahNeural")
    engine = resolve_tts_engine()
    if engine == "edge-tts":
        return CONFIG.get("voice_name", "en-US-JennyNeural")
    if engine == "kokoro":
        return CONFIG.get("voice_name", "bf_emma")
    return CONFIG.get("voice_name", "en_US-lessac-medium")


def resolve_active_voice():
    return resolve_voice_for_text("")


def set_reply_language(text, whisper_lang=None):
    """Update per-turn language used by prompts, tools, and TTS."""
    global current_reply_lang
    current_reply_lang = detect_language_from_text(text, whisper_lang)
    try:
        global_scheduler.language = current_reply_lang
    except Exception:
        pass
    print(f"[LANG] Reply language → {current_reply_lang}")
    return current_reply_lang


def resolve_whisper_model():
    """Multilingual tiny when language is auto/Arabic; allow tiny.en only for fixed English."""
    configured = CONFIG.get("whisper_model", "tiny")
    cfg = str(CONFIG.get("language", "auto")).lower()
    if cfg in ("en", "english") and configured:
        return configured
    if configured.endswith(".en"):
        return "tiny"
    return configured or "tiny"


def build_system_prompt(base_personality, extra="", lang=None):
    """Language-aware system prompt; optional private reasoning for better answers."""
    code = str(lang or current_reply_lang or CONFIG.get("language", "en")).lower()
    if code == "auto":
        code = current_reply_lang
    if code in ("ar", "ar_iq", "ar-iq", "iraqi", "arabic") or code.startswith("ar"):
        lang_rule = (
            "CRITICAL LANGUAGE: Respond in clear, natural Modern Standard Arabic (الفصحى المعاصرة). "
            "Use widely understood Arabic; avoid regional slang unless the user uses it first. "
            "Keep answers clear and useful. Reply only in Arabic."
        )
    else:
        lang_rule = (
            "CRITICAL LANGUAGE: Respond in clear, natural English. "
            "Match the user's language if they clearly use another language."
        )
    accuracy = (
        "CRITICAL UNDERSTANDING: The user text may come from imperfect speech-to-text. "
        "Infer the most likely intended meaning from context and phonetic typos "
        "(e.g. 'write'/'right', 'weather'/'whether'). "
        "Answer helpfully and accurately. If unsure, say so — never invent facts, prices, or news."
    )
    if CONFIG.get("enable_reasoning", False):
        think_rule = (
            "Optional: one short <think>...</think> then the spoken answer only."
        )
    else:
        think_rule = (
            "Answer directly. No <think> tags, no chain-of-thought, no preamble."
        )
    style = (
        "STYLE: Voice assistant Bayan. Max 2 short sentences. No lists unless asked."
    )
    parts = [base_personality, lang_rule, accuracy, think_rule, style]
    if extra:
        parts.append(extra)
    return "\n".join(parts)


# --- 2. MEMORY SYSTEM (deferred for faster startup) ---
memory_mgr = None
_memory_init_lock = threading.Lock()


def ensure_memory_manager():
    """Lazy-init ChromaDB after the voice loop is ready."""
    global memory_mgr
    if memory_mgr is not None or not CONFIG.get("enable_memory"):
        return memory_mgr
    with _memory_init_lock:
        if memory_mgr is not None:
            return memory_mgr
        try:
            from memory_manager import MemoryManager
            memory_mgr = MemoryManager(
                db_path=CONFIG["memory_db_path"],
                llm_model=CONFIG["llm_model"],
                num_thread=CONFIG["num_thread"],
                summarization_threshold=CONFIG["memory_summarization_threshold"]
            )
            print("[INIT] MemoryManager (ChromaDB) successfully loaded.")
        except Exception as e:
            print(f"[INIT Warning] Failed to initialize MemoryManager: {e}. Memory disabled.")
            CONFIG["enable_memory"] = False
    return memory_mgr


# --- 3. THE SIGNAL BRIDGE ---
class AIEngineSignals(QObject):
    state_changed = Signal(str)
    subtitle_changed = Signal(str)
    chat_log_append = Signal(str, str)

signals = AIEngineSignals()

# --- 4. AUDIO STREAM PLAYER (NON-BLOCKING) ---
class AudioStreamPlayer:
    def __init__(self, sample_rate=24000):
        self.sample_rate = sample_rate
        self.audio_data_queue = queue.Queue()
        self.current_chunk = np.array([], dtype=np.float32)
        self.current_idx = 0
        self.lock = threading.Lock()
        # Wall-clock estimate of when speakers finish (fixes early unmute / self-hearing)
        self.playing_until = 0.0
        self.queued_samples = 0
        
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype='float32',
            callback=self._callback
        )
        self.stream.start()
        
    def _callback(self, outdata, frames, time_info, status):
        out_len = 0
        while out_len < frames:
            if len(self.current_chunk) == 0 or self.current_idx >= len(self.current_chunk):
                try:
                    self.current_chunk = self.audio_data_queue.get_nowait()
                    self.current_idx = 0
                except queue.Empty:
                    outdata[out_len:] = 0
                    break
            
            available = len(self.current_chunk) - self.current_idx
            to_copy = min(available, frames - out_len)
            outdata[out_len : out_len + to_copy, 0] = self.current_chunk[self.current_idx : self.current_idx + to_copy]
            self.current_idx += to_copy
            out_len += to_copy
            with self.lock:
                self.queued_samples = max(0, self.queued_samples - to_copy)

    def play(self, numpy_array):
        arr = numpy_array.astype(np.float32)
        n = len(arr)
        with self.lock:
            now = time.time()
            base = max(now, self.playing_until)
            self.playing_until = base + (n / float(self.sample_rate)) + 0.05
            self.queued_samples += n
        self.audio_data_queue.put(arr)

    def is_playing(self):
        with self.lock:
            if time.time() < self.playing_until:
                return True
            if self.queued_samples > 0:
                return True
            if not self.audio_data_queue.empty():
                return True
            if len(self.current_chunk) > 0 and self.current_idx < len(self.current_chunk):
                return True
            return False

    def clear(self):
        with self.lock:
            self.current_chunk = np.array([], dtype=np.float32)
            self.current_idx = 0
            self.playing_until = 0.0
            self.queued_samples = 0
            while not self.audio_data_queue.empty():
                try:
                    self.audio_data_queue.get_nowait()
                except queue.Empty:
                    pass

# --- 5. THE AUDIO QUEUES & MUTEXES ---
audio_queue = queue.Queue()
tts_queue = queue.Queue()
audio_player = AudioStreamPlayer(sample_rate=24000)

import scheduler
global_scheduler = scheduler.BackgroundScheduler(
    tts_queue, language=str(CONFIG.get("language", "en"))
)
global_scheduler.start()

is_processing = False
interrupt_flag = False
system_ready = False
wake_blocked_until = 0.0
last_activity_time = time.time()

cached_greetings = []       # English wake greetings (wake word is English)
cached_acks = {"en": [], "ar": []}
cached_fillers = {"en": [], "ar": []}
cached_idle_lines = {"en": [], "ar": []}


def block_wake_listening(extra_sec=None):
    """Prevent wake-word / mic capture from hearing TTS echo."""
    global wake_blocked_until
    cooldown = CONFIG.get("wake_word_cooldown_sec", 2.0)
    if extra_sec is not None:
        cooldown = max(cooldown, float(extra_sec))
    wake_blocked_until = time.time() + float(cooldown)


def flush_audio_queue():
    while not audio_queue.empty():
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            break


def emergency_kill_switch(signum, frame):
    global is_processing, interrupt_flag, last_activity_time
    
    print("\n[KILL SWITCH ACTIVATED] Purging thoughts and audio...")
    interrupt_flag = True
    audio_player.clear()
    
    while not tts_queue.empty():
        try:
            tts_queue.get_nowait()
            tts_queue.task_done()
        except queue.Empty:
            pass
            
    signals.state_changed.emit("idle")
    signals.subtitle_changed.emit("")
    block_wake_listening()
    flush_audio_queue()
    last_activity_time = time.time()
    print("Listening...")


def check_bot_is_speaking():
    return audio_player.is_playing() or time.time() < wake_blocked_until


def play_audio_blocking(samples):
    audio_player.play(samples)
    while audio_player.is_playing() and not interrupt_flag:
        time.sleep(0.02)
    block_wake_listening()
    flush_audio_queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    # Soft-mute mic while speaking, processing, or in post-TTS cooldown
    if not check_bot_is_speaking() and not is_processing:
        audio_queue.put(bytes(indata))


def get_tts_lang_code(text=""):
    if arabic_char_ratio(text) >= 0.2 or (not text and is_arabic_language()):
        return "ar"
    return "en-us"


def pick_cached_audio(cache_map):
    """Pick from language-specific cache list."""
    key = "ar" if is_arabic_language() else "en"
    bucket = cache_map.get(key) or cache_map.get("en") or []
    if not bucket:
        return None
    return random.choice(bucket)


def tts_worker_loop(kokoro):
    global interrupt_flag, last_activity_time
    while True:
        sentence = tts_queue.get()
        try:
            if sentence == "<END_OF_RESPONSE>":
                while audio_player.is_playing() and not interrupt_flag:
                    time.sleep(0.05)
                block_wake_listening()
                flush_audio_queue()
                signals.state_changed.emit("idle")
                signals.subtitle_changed.emit("")
                last_activity_time = time.time()
                if CONFIG["mode"] == "gui":
                    print("\nListening...")
                continue
                
            if sentence.strip():
                voice = resolve_voice_for_text(sentence)
                lang_code = get_tts_lang_code(sentence)
                want_ar = arabic_char_ratio(sentence) >= 0.2 or str(lang_code).startswith("ar")
                speed = float(
                    CONFIG.get("arabic_voice_speed", 0.95) if want_ar
                    else CONFIG.get("voice_speed", 1.05)
                )
                print(f"\n[Synthesizing/{voice}]: {sentence}")
                signals.subtitle_changed.emit(sentence)
                samples, sample_rate = kokoro.create(
                    sentence,
                    voice=voice,
                    speed=speed,
                    lang=lang_code
                )
                if samples is None or not _audio_is_usable(samples):
                    print(f"[TTS] Skipping unusable audio for: {sentence[:40]}")
                    continue
                # Resample player if Edge-TTS returns a different rate
                if sample_rate and sample_rate != audio_player.sample_rate:
                    duration = len(samples) / float(sample_rate)
                    target_len = int(duration * audio_player.sample_rate)
                    if target_len > 0:
                        x_old = np.linspace(0, 1, len(samples), endpoint=False)
                        x_new = np.linspace(0, 1, target_len, endpoint=False)
                        samples = np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.float32)
                signals.state_changed.emit("speaking")
                audio_player.play(samples)
                
        except Exception as e:
            print(f"\n[Voice Engine Error]: {str(e)}")
        finally:
            tts_queue.task_done()


# --- WEB SEARCH (improved) ---
FACTUAL_SEARCH_TRIGGERS = [
    "search", "google", "look up", "weather", "latest", "news", "price", "today", "current",
    "who is", "who was", "when did", "when was", "how many", "how much",
    "score", "result", "stock", "crypto", "temperature", "forecast",
    "definition of", "meaning of", "according to", "wikipedia",
    "اخبار", "طقس", "سعر", "اليوم", "حاليا", "بحث", "من هو", "ما هو", "وين يقع",
]

# "what is X" often needs search, but not identity / math / local tools
_SEARCH_WHAT_IS_SKIP = (
    "your name", "who are you", "what are you", "what can you", "what do you",
    "my name", "this mean", "wrong with",
)


def needs_web_search(command_lower, command_original=""):
    if any(s in command_lower for s in _SEARCH_WHAT_IS_SKIP):
        return False
    if any(word in command_lower for word in FACTUAL_SEARCH_TRIGGERS):
        return True
    # Broad factual "what is / what's" → search (tools already caught math)
    if re.search(r"\b(what(?:'s| is| are)|whats)\b", command_lower):
        if not re.search(r"\d", command_lower):  # leave pure math to calc tool / LLM
            return True
    ar_bits = [
        "هل هناك خبر", "في خبر", "كم سعر", "نتيجة", "مباراة",
        "ما معنى", "من هو", "أين صارت", "أين يقع",
    ]
    return any(b in command_original for b in ar_bits)


def clean_search_query(command):
    query = command.strip()
    # Strip wake / filler words (EN + AR)
    drop = [
        "hey assistant", "computer", "jarvis", "system wake", "alexa",
        "please", "can you", "could you", "tell me", "i want to know",
        "search for", "search", "google", "look up", "find out",
        "الرجاء", "من فضلك", " downstream عن", "ابحث عن", "شوف لي", "قلي",
    ]
    lower = query.lower()
    for word in drop:
        if word in lower:
            # case-insensitive replace
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            query = pattern.sub(" ", query)
            lower = query.lower()
    query = re.sub(r"\s+", " ", query).strip(" ?!.،؟")
    return query or command.strip()


def search_internet(query):
    """DuckDuckGo text + news; richer snippets for the LLM."""
    print(f"[WEB SEARCH]: Browsing for '{query}'...")
    try:
        blocks = []
        with DDGS() as ddgs:
            text_results = list(ddgs.text(query, max_results=5)) or []
            news_results = []
            try:
                news_results = list(ddgs.news(query, max_results=3)) or []
            except Exception:
                news_results = []

        for i, r in enumerate(text_results, 1):
            title = r.get("title") or ""
            body = r.get("body") or r.get("snippet") or ""
            href = r.get("href") or r.get("link") or ""
            blocks.append(f"[{i}] {title}\n{body}\nURL: {href}")

        for i, r in enumerate(news_results, 1):
            title = r.get("title") or ""
            body = r.get("body") or ""
            date = r.get("date") or ""
            blocks.append(f"[News {i}] {title} ({date})\n{body}")

        if not blocks:
            return "No search results found."
        return "\n\n".join(blocks)
    except Exception as e:
        return f"Failed to search internet: {str(e)}"


def play_thinking_fillers(filler_cache):
    global is_processing
    
    time_spent = 0
    while is_processing and time_spent < 40:
        time.sleep(0.1)
        time_spent += 1
        
    current_delay_seconds = 6.0
    
    while is_processing:
        pick = None
        if isinstance(filler_cache, dict):
            pick = pick_cached_audio(filler_cache)
        elif filler_cache:
            pick = random.choice(filler_cache)
        if not check_bot_is_speaking() and pick:
            chosen_audio, chosen_sr = pick
            play_audio_blocking(chosen_audio)
            
        time_spent = 0
        wait_ticks = int(current_delay_seconds * 10)
        
        while is_processing and time_spent < wait_ticks:
            time.sleep(0.1)
            time_spent += 1
            
        current_delay_seconds += 3.0
        if current_delay_seconds > 15.0:
            current_delay_seconds = 15.0


def text_echo_overlap(a, b):
    """Return overlap ratio of tokens between two strings (echo detection)."""
    if not a or not b:
        return 0.0
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(max(len(ta), len(tb)))


def record_and_transcribe_whisper(whisper_model):
    """Records audio using VAD silence detection and transcribes via Whisper."""
    print("[VAD] Recording user command...")
    recording_buffers = []
    silence_ticks = 0
    speech_detected = False
    max_silence_ticks = int(1.5 / 0.5)
    max_listen_ticks = int(15.0 / 0.5)
    listen_ticks = 0
    
    flush_audio_queue()
            
    while listen_ticks < max_listen_ticks:
        try:
            chunk_data = audio_queue.get(timeout=2.0)
        except queue.Empty:
            break
            
        chunk_np = np.frombuffer(chunk_data, dtype=np.int16)
        recording_buffers.append(chunk_np)
        listen_ticks += 1
        
        float_chunk = chunk_np.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(float_chunk**2))
        
        if rms > CONFIG.get("vad_threshold", 0.02):
            if not speech_detected:
                print("[VAD] Speech started...")
            speech_detected = True
            silence_ticks = 0
        else:
            if speech_detected:
                silence_ticks += 1
                
        if speech_detected and silence_ticks >= max_silence_ticks:
            print("[VAD] Speech ended (silence threshold hit).")
            break
            
        if not speech_detected and listen_ticks >= int(4.0 / 0.5):
            print("[VAD] No speech detected (timeout).")
            break
            
    if recording_buffers and speech_detected:
        full_audio = np.concatenate(recording_buffers)
        float_audio = full_audio.astype(np.float32) / 32768.0
        
        print("[STT] Transcribing voice command...")
        try:
            kwargs = {"beam_size": 1}
            cfg_lang = str(CONFIG.get("language", "auto")).lower()
            # Force only when config locks a language; auto = let Whisper detect
            if cfg_lang in ("en", "english"):
                kwargs["language"] = "en"
            elif cfg_lang.startswith("ar"):
                kwargs["language"] = "ar"
            segments, info = whisper_model.transcribe(float_audio, **kwargs)
            transcribed_text = " ".join([seg.text for seg in segments]).strip()
            if len(transcribed_text) < 2:
                return ""
            detected = getattr(info, "language", None)
            if detected:
                print(f"[STT] Whisper language guess: {detected}")
            # Stash on function attribute for process_and_respond
            record_and_transcribe_whisper.last_whisper_lang = detected
            return transcribed_text
        except Exception as ex:
            print(f"[STT Error] Whisper transcription failed: {ex}")
            
    return ""


class PiperBilingualTTS:
    """Offline EN + AR TTS via Piper ONNX voices (no Wi‑Fi)."""

    def __init__(self, voices_dir, en_name, ar_name):
        from piper import PiperVoice
        base = os.path.abspath(voices_dir)
        if not os.path.isabs(voices_dir):
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)), voices_dir)

        def _resolve(name):
            if os.path.isabs(name) and os.path.exists(name):
                return name
            path = os.path.join(base, name if name.endswith(".onnx") else f"{name}.onnx")
            if not os.path.exists(path):
                raise FileNotFoundError(f"Piper voice not found: {path}")
            return path

        en_path = _resolve(en_name)
        ar_path = _resolve(ar_name)
        print(f"[INIT] Piper EN voice: {en_path}")
        print(f"[INIT] Piper AR voice: {ar_path}")
        self.voices = {
            "en": PiperVoice.load(en_path),
            "ar": PiperVoice.load(ar_path),
        }
        self.en_name = en_name
        self.ar_name = ar_name

    def _pick_key(self, text, voice, lang):
        voice_s = str(voice or "").lower()
        lang_s = str(lang or "").lower()
        if lang_s.startswith("ar") or "ar_" in voice_s or voice_s.startswith("ar"):
            return "ar"
        if "en_" in voice_s or voice_s in ("en", "en-us", "en_us"):
            return "en"
        if arabic_char_ratio(text or "") >= 0.2:
            return "ar"
        if is_arabic_language():
            return "ar"
        return "en"

    def create(self, text, voice=None, speed=1.0, lang=None):
        from piper import SynthesisConfig
        key = self._pick_key(text, voice, lang)
        pv = self.voices[key]
        length_scale = 1.0 / max(0.5, float(speed or 1.0))
        syn = SynthesisConfig(length_scale=length_scale)
        try:
            chunks = list(pv.synthesize(text or " ", syn_config=syn))
        except Exception as e:
            print(f"[Piper Error] Failed to synthesize ({key}): {e}")
            return np.zeros(22050, dtype=np.float32), 22050
        if not chunks:
            return np.zeros(22050, dtype=np.float32), 22050
        audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        return audio, int(chunks[0].sample_rate)


def _audio_is_usable(samples, min_samples=500, min_rms=1e-4):
    """Reject silence / failed TTS buffers so we can fall back."""
    if samples is None:
        return False
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    if arr.size < min_samples:
        return False
    return float(np.sqrt(np.mean(arr * arr))) >= min_rms


class EdgeTTSWrapper:
    def __init__(self):
        pass

    def create(self, text, voice, speed=1.0, lang=None):
        import asyncio
        import edge_tts
        import io
        import soundfile as sf

        rate_percent = int((speed - 1.0) * 100)
        rate_str = f"{rate_percent:+d}%" if rate_percent != 0 else "+0%"

        async def _async_gen():
            communicate = edge_tts.Communicate(text, voice, rate=rate_str)
            mp3_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_bytes += chunk["data"]
            return mp3_bytes

        try:
            mp3_bytes = asyncio.run(_async_gen())
            if not mp3_bytes or len(mp3_bytes) < 64:
                raise RuntimeError("Edge-TTS returned empty audio")
            buffer = io.BytesIO(mp3_bytes)
            data, samplerate = sf.read(buffer)
            samples = data.astype(np.float32)
            if not _audio_is_usable(samples):
                raise RuntimeError("Edge-TTS returned near-silent audio")
            return samples, samplerate
        except Exception as e:
            print(f"[Edge-TTS Error] Failed to synthesize '{text}': {e}")
            # None signals failure (do NOT return silence — callers must fall back)
            return None, None


class HybridTTS:
    """English via Piper (offline); Arabic via Edge-TTS female neural voice (needs Wi‑Fi)."""

    def __init__(self, piper_engine, edge_engine, piper_ar_fallback=None):
        self.piper = piper_engine
        self.edge = edge_engine
        self.piper_ar = piper_ar_fallback  # offline Piper if Edge fails
        self._edge_fail_logged = False

    def create(self, text, voice=None, speed=1.0, lang=None):
        want_ar = arabic_char_ratio(text or "") >= 0.2 or str(lang or "").startswith("ar")
        if want_ar and resolve_arabic_tts_engine() == "edge-tts":
            ar_voice = CONFIG.get("arabic_voice_name", "ar-SA-ZariyahNeural")
            try:
                samples, sr = self.edge.create(text, ar_voice, speed=speed, lang="ar")
                if _audio_is_usable(samples):
                    return samples, sr
                if not self._edge_fail_logged:
                    print("[HybridTTS] Edge Arabic failed; using Piper Arabic fallback.")
                    self._edge_fail_logged = True
            except Exception as e:
                if not self._edge_fail_logged:
                    print(f"[HybridTTS] Edge Arabic unavailable ({e}); using Piper Arabic fallback.")
                    self._edge_fail_logged = True
            if self.piper_ar or self.piper:
                fb = self.piper_ar or self.piper
                return fb.create(
                    text,
                    CONFIG.get("piper_arabic_fallback", "ar_JO-kareem-medium"),
                    speed,
                    "ar",
                )
            return np.zeros(22050, dtype=np.float32), 22050
        # English / other → Piper
        en_voice = voice or CONFIG.get("voice_name", "en_US-lessac-medium")
        return self.piper.create(text, en_voice, speed, lang or "en-us")


class ThinkBlockFilter:
    def __init__(self):
        self.in_think = False
        self.buffer = ""
        
    def filter(self, token):
        self.buffer += token
        
        if not self.in_think:
            if "<think>" in self.buffer:
                parts = self.buffer.split("<think>", 1)
                normal_output = parts[0]
                self.in_think = True
                self.buffer = parts[1]
                return normal_output
            else:
                hold_back = 0
                for i in range(1, min(8, len(self.buffer) + 1)):
                    suffix = self.buffer[-i:]
                    if "<think>".startswith(suffix):
                        hold_back = i
                        break
                if hold_back > 0:
                    output = self.buffer[:-hold_back]
                    self.buffer = self.buffer[-hold_back:]
                    return output
                else:
                    output = self.buffer
                    self.buffer = ""
                    return output
        else:
            if "</think>" in self.buffer:
                parts = self.buffer.split("</think>", 1)
                self.in_think = False
                self.buffer = parts[1]
                return self.filter("")
            else:
                hold_back = 0
                for i in range(1, min(10, len(self.buffer) + 1)):
                    suffix = self.buffer[-i:]
                    if "</think>".startswith(suffix):
                        hold_back = i
                        break
                if hold_back > 0:
                    self.buffer = self.buffer[-hold_back:]
                else:
                    self.buffer = ""
                return ""


# --- COGNITIVE REASONING PIPELINE ---
last_user_command = ""
last_ai_response = ""


def strip_think_blocks(text):
    """Remove private reasoning tags before memory / UI / echo-guard."""
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def stream_llm_to_tts(system_msg, user_content):
    """Stream Ollama tokens into TTS; return spoken response (no <think>)."""
    global interrupt_flag
    full_ai_response = ""
    interrupt_flag = False

    opts = {
        "num_thread": int(CONFIG.get("num_thread", 2)),
        "num_ctx": int(CONFIG.get("num_ctx", 2048)),
        "num_predict": int(CONFIG.get("num_predict", 120)),
        "temperature": 0.4,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    }
    stream_response = ollama.chat(
        model=CONFIG["llm_model"],
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ],
        stream=True,
        options=opts,
    )

    sentence_buffer = ""
    print("[AI]: ", end="", flush=True)
    think_filter = ThinkBlockFilter()
    printed_think = False

    for chunk in stream_response:
        if interrupt_flag:
            print("\n[Thought Process Terminated]")
            break

        token = chunk["message"]["content"]
        full_ai_response += token

        # Show when model is reasoning (console only; not spoken)
        if "<think>" in token and not printed_think:
            print("\n[reasoning...] ", end="", flush=True)
            printed_think = True
        if "</think>" in token and printed_think:
            print("\n[AI]: ", end="", flush=True)
            printed_think = False

        filtered_token = think_filter.filter(token)
        if not filtered_token:
            continue

        print(filtered_token, end="", flush=True)
        sentence_buffer += filtered_token

        if any(punct in filtered_token for punct in [".", "!", "?", "\n", "。", "؟"]):
            clean_sentence = sentence_buffer.strip()
            if clean_sentence:
                tts_queue.put(clean_sentence)
            sentence_buffer = ""

    if not interrupt_flag and sentence_buffer.strip():
        tts_queue.put(sentence_buffer.strip())

    tts_queue.put("<END_OF_RESPONSE>")
    if interrupt_flag:
        return ""
    return strip_think_blocks(full_ai_response)


def process_and_respond(command):
    global is_processing, interrupt_flag
    global last_user_command, last_ai_response, last_activity_time

    if is_processing:
        print("[GUARD] Ignoring command while already processing.")
        return

    # Echo guard: ignore STT that mostly repeats last AI line
    if last_ai_response and text_echo_overlap(command, last_ai_response) >= 0.55:
        print(f"[ECHO GUARD] Ignoring likely self-hearing: '{command}'")
        block_wake_listening()
        flush_audio_queue()
        return

    clean_cmd = command.strip().lower().rstrip(".").rstrip("?")
    stop_words = ["stop", "go to sleep", "cancel", "shut up", "انصرف", "اسكت", "توقف", "نام"]
    if clean_cmd in stop_words:
        emergency_kill_switch(None, None)
        return

    # Detect reply language from this utterance (auto / fixed via config)
    whisper_hint = getattr(record_and_transcribe_whisper, "last_whisper_lang", None)
    lang = set_reply_language(command, whisper_hint)
    record_and_transcribe_whisper.last_whisper_lang = None

    ensure_memory_manager()
    if memory_mgr and last_user_command and last_ai_response:
        memory_mgr.detect_and_save_correction(command, last_user_command, last_ai_response)

    signals.chat_log_append.emit("User", command)
    flush_audio_queue()
    signals.state_changed.emit("processing")
    last_activity_time = time.time()

    ack = pick_cached_audio(cached_acks)
    if ack:
        chosen_audio, chosen_sr = ack
        play_audio_blocking(chosen_audio)
        time.sleep(0.1)

    print("Thinking...")
    is_processing = True
    # Fillers steal CPU from the LLM on weak laptops — off by default
    if CONFIG.get("enable_thinking_fillers", False):
        threading.Thread(target=play_thinking_fillers, args=(cached_fillers,), daemon=True).start()

    try:
        command_lower = command.lower()

        # --- Notes: "remember that ..." / "تذكر ان ..." ---
        note_m = re.match(
            r"(?:remember(?:\s+that)?|note(?:\s+that)?|save(?:\s+this)?)\s+(.+)",
            command_lower
        )
        note_ar = re.search(r"(?:تذكر|احفظ|خلّي ببالك|خليه ببالك)\s*(?:ان|أن|ب)?\s*(.+)", command)
        if note_m or note_ar:
            note_text = (note_m.group(1) if note_m else note_ar.group(1)).strip()
            mgr = ensure_memory_manager()
            if mgr:
                mgr.add_interaction(f"[NOTE] {note_text}", "Saved.")
                reply = "تمام، حفظتها." if is_arabic_language() else f"Got it. I'll remember: {note_text}"
            else:
                reply = "الذاكرة مطفية." if is_arabic_language() else "Memory is disabled."
            tts_queue.put(reply)
            tts_queue.put("<END_OF_RESPONSE>")
            return

        # --- PRE-ROUTE 1: SCHEDULER REMINDERS / ALARMS ---
        if CONFIG.get("enable_reminders"):
            sched_response = scheduler.execute_scheduler_if_matched(
                command, global_scheduler, language=lang
            )
            if sched_response:
                print(f"[TOOL] Scheduled: {sched_response}")
                tts_queue.put(sched_response)
                tts_queue.put("<END_OF_RESPONSE>")
                if memory_mgr:
                    memory_mgr.add_interaction(command, sched_response)
                return

        # --- PRE-ROUTE 2: LOCAL SYSTEM TOOLS ---
        if CONFIG.get("enable_tools"):
            import tools
            tool_response = tools.execute_command_if_matched(
                command, language=lang, scheduler=global_scheduler
            )
            if tool_response:
                print(f"[TOOL] Executed command: {tool_response}")
                tts_queue.put(tool_response)
                tts_queue.put("<END_OF_RESPONSE>")
                if memory_mgr:
                    memory_mgr.add_interaction(command, tool_response)
                return

        full_ai_response = ""
        smart_base = (
            "You are Bayan, a sharp local voice assistant on the user's laptop. "
            "Understand messy speech-to-text. Prefer correct meaning over literal typos. "
            "Be concise, concrete, and useful — never vague filler."
        )
        if CONFIG.get("sarcastic_personality", False):
            smart_base += " Light dry wit is OK; never sacrifice clarity or accuracy."

        # --- ROUTE 1: SCREEN ---
        if "screen" in command_lower or "الشاشة" in command:
            print("Routing to Moondream (Screen Analysis)...")
            img_path = capture_wayland_screen()
            ai_text = analyze_visual_input(command, img_path, CONFIG["vision_model"])
            tts_queue.put(ai_text)
            tts_queue.put("<END_OF_RESPONSE>")
            if memory_mgr and ai_text:
                memory_mgr.add_interaction(command, ai_text)

        # --- ROUTE 2: WEBCAM ---
        elif any(trigger in command_lower for trigger in ["camera", "look at this", "holding"]) or "الكاميرا" in command:
            print("Routing to Moondream (Webcam Analysis)...")
            img_path = capture_hardware_webcam()
            ai_text = analyze_visual_input(command, img_path, CONFIG["vision_model"])
            tts_queue.put(ai_text)
            tts_queue.put("<END_OF_RESPONSE>")
            if memory_mgr and ai_text:
                memory_mgr.add_interaction(command, ai_text)

        # --- ROUTE 3: WEB SEARCH ---
        elif needs_web_search(command_lower, command):
            print("Routing to Ollama (Web Search Augmented)...")
            query_to_search = clean_search_query(command)
            web_context = search_internet(query_to_search)

            memory_context = ""
            if memory_mgr:
                memory_context = memory_mgr.retrieve_context(command)

            search_extra = (
                "Use ONLY the live internet data below to answer. "
                "Synthesize a clear spoken answer; cite which sources agree if they conflict. "
                "If results are empty or irrelevant, say you could not find reliable info.\n\n"
                f"SEARCH QUERY: {query_to_search}\n\nWEB DATA:\n{web_context}"
            )
            if memory_context:
                search_extra += f"\n\nUser memories if relevant:\n{memory_context}"

            system_msg = build_system_prompt(smart_base, search_extra, lang=lang)
            full_ai_response = stream_llm_to_tts(system_msg, command)

            if memory_mgr and full_ai_response:
                memory_mgr.add_interaction(command, full_ai_response)
            if full_ai_response:
                last_user_command = command
                last_ai_response = full_ai_response
                signals.chat_log_append.emit("AI", full_ai_response)

        # --- ROUTE 4: STANDARD LLM ---
        else:
            print("Routing to Ollama (Logical Reasoning - STREAMING)...")
            memory_context = ""
            if memory_mgr:
                memory_context = memory_mgr.retrieve_context(command)

            extra = ""
            if memory_context:
                extra = f"User memories if relevant:\n{memory_context}"
            extra = (extra + "\n" if extra else "") + (
                "If the question needs live/current data you do not have, say so briefly "
                "and suggest asking with words like 'search' or 'latest'."
            )

            system_msg = build_system_prompt(smart_base, extra, lang=lang)
            full_ai_response = stream_llm_to_tts(system_msg, command)

            if memory_mgr and full_ai_response:
                memory_mgr.add_interaction(command, full_ai_response)
            if full_ai_response:
                last_user_command = command
                last_ai_response = full_ai_response
                signals.chat_log_append.emit("AI", full_ai_response)

    except Exception as e:
        print(f"\n[SENSORY ERROR]: {str(e)}")
        err = "المعذرة، حساساتي حالياً متعطلة." if is_arabic_language() else \
              "I am sorry, but my optical or data sensors are currently blocked."
        tts_queue.put(err)
        tts_queue.put("<END_OF_RESPONSE>")
    finally:
        tts_queue.join()
        block_wake_listening(0.8)
        flush_audio_queue()
        is_processing = False
        last_activity_time = time.time()


# --- VOICE PHRASE CACHE (disk) ---
def _voice_cache_dir():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".voice_cache")
    os.makedirs(base, exist_ok=True)
    return base


def _phrase_cache_key(phrase, voice, speed, lang):
    raw = f"{phrase}|{voice}|{speed}|{lang}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def synthesize_cached(kokoro, phrase, voice, speed, lang):
    """Load phrase audio from disk cache or synthesize and save."""
    path = os.path.join(_voice_cache_dir(), f"{_phrase_cache_key(phrase, voice, speed, lang)}.npz")
    if os.path.exists(path):
        try:
            data = np.load(path)
            cached = np.asarray(data["audio"], dtype=np.float32)
            # Drop silent/corrupt caches from earlier Edge-TTS 403 failures
            if _audio_is_usable(cached):
                return cached, float(data["sr"])
            os.remove(path)
        except Exception:
            pass
    audio, sr = kokoro.create(phrase, voice=voice, speed=speed, lang=lang)
    if _audio_is_usable(audio):
        try:
            np.savez_compressed(path, audio=np.asarray(audio, dtype=np.float32), sr=np.array(sr))
        except Exception as e:
            print(f"[CACHE Warning] Could not save phrase cache: {e}")
    return audio, sr


def wake_phrase_at_boundary(transcribed_text, wake_words):
    """True if a wake phrase appears as a whole word/phrase near the start."""
    text = transcribed_text.lower().strip()
    for ww in wake_words:
        ww = ww.lower().strip()
        if not ww:
            continue
        # Start of utterance or after short filler
        if text == ww or text.startswith(ww + " ") or text.startswith(ww + ","):
            return True
        # Whole-word regex (spaces / underscores normalized)
        pattern = r"(?:^|\s)" + re.escape(ww).replace(r"\ ", r"[\s_]+") + r"(?:\s|$|,)"
        if re.search(pattern, text):
            # Prefer early position (first 40 chars)
            idx = text.find(ww.replace("_", " "))
            if idx == -1:
                idx = text.find(ww)
            if idx != -1 and idx <= 40:
                return True
    return False


def idle_chatter_loop():
    """Occasionally speak when idle to grab attention (demo mode)."""
    global last_activity_time
    while True:
        time.sleep(5)
        if not CONFIG.get("enable_idle_chatter"):
            continue
        if not system_ready or is_processing or check_bot_is_speaking():
            continue
        min_s = float(CONFIG.get("idle_chatter_min_sec", 90))
        max_s = float(CONFIG.get("idle_chatter_max_sec", 180))
        if max_s < min_s:
            max_s = min_s
        # Wait until idle long enough; randomize target each cycle
        idle_for = time.time() - last_activity_time
        target = random.uniform(min_s, max_s)
        if idle_for < target:
            continue
        bucket = cached_idle_lines.get("ar" if is_arabic_language() else "en") or cached_idle_lines.get("en") or []
        if not bucket:
            continue
        print("[IDLE] Speaking to get attention...")
        phrase = random.choice(bucket)
        signals.state_changed.emit("speaking")
        tts_queue.put(phrase)
        tts_queue.put("<END_OF_RESPONSE>")
        # Prevent immediate re-fire
        last_activity_time = time.time()
        time.sleep(2)


# --- MASTER COGNITIVE LOOP ---
def real_cognitive_loop():
    global is_processing, system_ready, last_activity_time
    global cached_greetings, cached_acks, cached_fillers, cached_idle_lines
    
    print("Booting Cognitive Engines (STT, TTS, Models)... Please wait.")
    
    use_whisper = CONFIG.get("stt_engine") == "whisper"
    use_oww = CONFIG.get("wake_word_engine") == "openwakeword"
    
    oww_model = None
    whisper_model = None
    vosk_model = None
    rec = None
    kokoro = None

    def load_oww():
        nonlocal oww_model, use_oww
        if not use_oww:
            return
        print("[INIT] Loading openWakeWord engine...")
        from openwakeword.model import Model as OWWModel
        import openwakeword
        import inspect
        wake_word_name = CONFIG.get("wake_word_model", "hey_jarvis")
        local_model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{wake_word_name}.onnx")
        if os.path.exists(local_model_path):
            model_path = local_model_path
            print(f"[INIT] Found custom local wake word model: {local_model_path}")
        else:
            paths = [p for p in openwakeword.get_pretrained_model_paths() if wake_word_name in p]
            if not paths:
                raise ValueError(f"Could not find wake word model matching name '{wake_word_name}'")
            model_path = paths[0]
            print(f"[INIT] Loaded pretrained wake word model: {model_path}")
        sig = inspect.signature(OWWModel.__init__)
        if "wakeword_model_paths" in sig.parameters:
            oww_model = OWWModel(wakeword_model_paths=[model_path])
        else:
            oww_model = OWWModel(wakeword_models=[model_path])
        print("[INIT] openWakeWord engine successfully loaded.")

    def load_whisper():
        nonlocal whisper_model, use_whisper
        if not use_whisper:
            return
        model_name = resolve_whisper_model()
        print(f"[INIT] Loading Faster-Whisper ({model_name}) on {CONFIG['whisper_device']}...")
        from faster_whisper import WhisperModel
        whisper_model = WhisperModel(
            model_name,
            device=CONFIG["whisper_device"],
            compute_type="int8"
        )
        print("[INIT] Faster-Whisper engine successfully loaded.")

    def load_tts():
        nonlocal kokoro
        engine = resolve_tts_engine()
        ar_engine = resolve_arabic_tts_engine()
        piper_ar = CONFIG.get("piper_arabic_fallback", "ar_JO-kareem-medium")
        en_voice = CONFIG.get("voice_name", "en_US-lessac-medium")

        if engine == "piper" and ar_engine == "edge-tts":
            print("[INIT] Hybrid TTS: Piper EN (female) + Edge-TTS AR (female Zariyah)...")
            piper = PiperBilingualTTS(
                CONFIG.get("piper_voices_dir", "./piper_voices"),
                en_voice,
                piper_ar,  # offline fallback only (male); live AR uses Edge female
            )
            kokoro = HybridTTS(piper, EdgeTTSWrapper(), piper)
        elif engine == "piper":
            print("[INIT] Loading Piper offline bilingual TTS...")
            ar_voice = CONFIG.get("arabic_voice_name", piper_ar)
            # Edge Neural names are not Piper files — use fallback
            if "Neural" in str(ar_voice) or str(ar_voice).startswith("ar-"):
                ar_voice = piper_ar
            kokoro = PiperBilingualTTS(
                CONFIG.get("piper_voices_dir", "./piper_voices"),
                en_voice,
                ar_voice,
            )
        elif engine == "edge-tts":
            print("[INIT] Loading Edge-TTS speech synthesis engine...")
            kokoro = EdgeTTSWrapper()
        else:
            print("[INIT] Loading Kokoro speech synthesis engine...")
            from kokoro_onnx import Kokoro
            kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
        print(
            f"[INIT] TTS ready (en={engine}, ar={ar_engine}, "
            f"voice={resolve_active_voice()})"
        )

    # Parallel load for faster cold start on CPU laptops
    loaders = {
        "wake": load_oww,
        "whisper": load_whisper,
        "tts": load_tts,
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in loaders.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                fut.result()
            except Exception as e:
                print(f"[INIT Warning] Failed to load {name}: {e}")
                if name == "wake":
                    use_oww = False
                    oww_model = None
                elif name == "whisper":
                    use_whisper = False
                    whisper_model = None
                elif name == "tts":
                    raise

    if not use_whisper or not use_oww:
        wake_word_name = CONFIG.get("wake_word_model", "hey_jarvis")
        is_arabic_wake = any('\u0600' <= char <= '\u06FF' for char in wake_word_name)
        
        vosk_model_dir = "model"
        if is_arabic_wake or is_arabic_language():
            if os.path.exists("model_ar"):
                vosk_model_dir = "model_ar"
                print("[INIT] Loading Arabic Vosk model...")
            else:
                print("\n[INIT Warning] Arabic Vosk 'model_ar' not found; using English Vosk.")
                
        print(f"[INIT] Loading Vosk acoustic model from '{vosk_model_dir}'...")
        from vosk import Model, KaldiRecognizer
        vosk_model = Model(vosk_model_dir)
        rec = KaldiRecognizer(vosk_model, 16000)
        print("[INIT] Vosk acoustic model successfully loaded.")

    threading.Thread(target=tts_worker_loop, args=(kokoro,), daemon=True).start()

    stream = None
    try:
        stream = sd.RawInputStream(
            samplerate=16000, blocksize=8000, dtype='int16',
            channels=1, callback=audio_callback
        )
    except Exception as e:
        print(f"\n[Voice Engine Warning] Could not open audio input stream: {e}")
        print("[Voice Engine Warning] Voice activation is disabled. Running in text-only mode.")

    # Compact phrase sets (EN + AR); disk cache speeds later boots
    print("Caching voices (EN+AR small set + disk cache)...")
    speed = CONFIG["voice_speed"]
    en_voice = CONFIG.get("voice_name", "en_US-lessac-medium")
    ar_voice = CONFIG.get("arabic_voice_name", "ar-SA-ZariyahNeural")

    wake_en = ["I'm listening.", "Yes?", "Go ahead."]
    ack_en = ["On it.", "Got it.", "One moment."]
    filler_en = ["Still looking...", "Just a moment...", "Almost there..."]
    idle_en = [
        "Hey, I'm still here if you need me.",
        "Just checking in — say Bayan when you want something.",
        "Getting lonely over here. Got a question?",
    ]
    ack_ar = ["حاضر.", "تمام.", "لحظة."]
    filler_ar = ["دعني أتحقق.", "لحظة من فضلك.", "جاري التدقيق."]
    idle_ar = [
        "مرحبا، ما زلت هنا إذا احتجت شيئاً.",
        "أشعر بالملل قليلاً... هل لديك سؤال؟",
        "كيف حالك؟ أنا جاهز إذا احتجت شيئاً.",
    ]

    # Wake greeting stays English (wake word is English Alexa)
    for phrase in wake_en:
        audio, sr = synthesize_cached(kokoro, phrase, en_voice, speed, "en-us")
        cached_greetings.append((audio, sr))
    for phrase in ack_en:
        audio, sr = synthesize_cached(kokoro, phrase, en_voice, speed, "en-us")
        cached_acks["en"].append((audio, sr))
    for phrase in filler_en:
        audio, sr = synthesize_cached(kokoro, phrase, en_voice, speed, "en-us")
        cached_fillers["en"].append((audio, sr))
    cached_idle_lines["en"] = list(idle_en)

    ar_speed = float(CONFIG.get("arabic_voice_speed", 0.95))
    for phrase in ack_ar:
        audio, sr = synthesize_cached(kokoro, phrase, ar_voice, ar_speed, "ar")
        cached_acks["ar"].append((audio, sr))
    for phrase in filler_ar:
        audio, sr = synthesize_cached(kokoro, phrase, ar_voice, ar_speed, "ar")
        cached_fillers["ar"].append((audio, sr))
    cached_idle_lines["ar"] = list(idle_ar)

    # Background memory init + idle chatter
    threading.Thread(target=ensure_memory_manager, daemon=True).start()
    threading.Thread(target=idle_chatter_loop, daemon=True).start()

    if stream:
        print("\n[SYSTEM READY] Assistant is passively listening...")
    else:
        print("\n[SYSTEM READY] Assistant is ready (Text-only Mode)...")
        
    signals.state_changed.emit("idle")
    system_ready = True
    last_activity_time = time.time()
    is_awake = False

    threshold = float(CONFIG.get("wake_word_threshold", 0.55))
    min_consecutive = int(CONFIG.get("wake_min_consecutive", 2))

    if stream:
        try:
            with stream:
                if use_oww and use_whisper:
                    wake_word_name = CONFIG.get("wake_word_model", "hey_jarvis")
                    oww_buffer = np.array([], dtype=np.int16)
                    consecutive_hits = 0
                    while True:
                        data = audio_queue.get()
                        if time.time() < wake_blocked_until or is_processing or check_bot_is_speaking():
                            oww_buffer = np.array([], dtype=np.int16)
                            consecutive_hits = 0
                            continue

                        chunk_np = np.frombuffer(data, dtype=np.int16)
                        oww_buffer = np.concatenate((oww_buffer, chunk_np))
                        
                        while len(oww_buffer) >= 1280:
                            block = oww_buffer[:1280]
                            oww_buffer = oww_buffer[1280:]
                            
                            if time.time() < wake_blocked_until:
                                consecutive_hits = 0
                                continue

                            prediction = oww_model.predict(block)
                            confidence = 0.0
                            for key, val in prediction.items():
                                if wake_word_name in key:
                                    confidence = val
                                    break
                            
                            if confidence > threshold:
                                consecutive_hits += 1
                            else:
                                consecutive_hits = 0

                            if consecutive_hits < min_consecutive:
                                continue

                            consecutive_hits = 0
                            print(f"\n[WAKE WORD DETECTED] Confidence: {confidence:.2f}")
                            signals.state_changed.emit("listening")
                            last_activity_time = time.time()
                            
                            chosen_audio, chosen_sr = random.choice(cached_greetings)
                            play_audio_blocking(chosen_audio)
                            
                            flush_audio_queue()
                            oww_buffer = np.array([], dtype=np.int16)
                            
                            transcribed_text = record_and_transcribe_whisper(whisper_model)
                            if transcribed_text:
                                if last_ai_response and text_echo_overlap(transcribed_text, last_ai_response) >= 0.55:
                                    print(f"[ECHO GUARD] Discarding STT echo: '{transcribed_text}'")
                                else:
                                    print(f"[USER (voice)]: {transcribed_text}")
                                    process_and_respond(transcribed_text)
                                        
                            signals.state_changed.emit("idle")
                            last_activity_time = time.time()
                            if CONFIG["mode"] == "gui":
                                print("\nListening...")
                            flush_audio_queue()
                            block_wake_listening()
                            oww_buffer = np.array([], dtype=np.int16)
                            break
                else:
                    wake_word_name = CONFIG.get("wake_word_model", "hey_jarvis").lower()
                    wake_words = ["hey assistant", "computer", "jarvis", "system wake", wake_word_name]
                    if wake_word_name.startswith("hey_"):
                        wake_words.append(wake_word_name.replace("hey_", ""))
                        wake_words.append(wake_word_name.replace("hey_", "hey "))
                    elif "hey " in wake_word_name:
                        wake_words.append(wake_word_name.replace("hey ", ""))
                        wake_words.append(wake_word_name.replace("hey ", "hey_"))
                    
                    while True:
                        data = audio_queue.get()
                        if time.time() < wake_blocked_until or is_processing:
                            continue
                        
                        if rec.AcceptWaveform(data):
                            result = json.loads(rec.Result())
                            transcribed_text = result.get("text", "").lower()
                            
                            if transcribed_text:
                                if not is_awake:
                                    if wake_phrase_at_boundary(transcribed_text, wake_words):
                                        is_awake = True
                                        print("\n[AWAKE] Listening for command...")
                                        signals.state_changed.emit("listening")
                                        last_activity_time = time.time()
                                        
                                        chosen_audio, chosen_sr = random.choice(cached_greetings)
                                        play_audio_blocking(chosen_audio)
                                        time.sleep(0.2)
                                        
                                        flush_audio_queue()
                                        rec.Reset()
                                            
                                else:
                                    if whisper_model:
                                        command = record_and_transcribe_whisper(whisper_model)
                                        if command:
                                            if last_ai_response and text_echo_overlap(command, last_ai_response) >= 0.55:
                                                print(f"[ECHO GUARD] Discarding STT echo: '{command}'")
                                            else:
                                                print(f"\n[USER (voice - whisper hybrid)]: {command}")
                                                process_and_respond(command)
                                    else:
                                        command = transcribed_text.strip()
                                        print(f"\n[USER (voice - vosk fallback)]: {command}")
                                        process_and_respond(command)
                                        
                                    is_awake = False
                                    signals.state_changed.emit("idle")
                                    last_activity_time = time.time()
                                    if CONFIG["mode"] == "gui":
                                        print("\nListening...")
                                    
                                    flush_audio_queue()
                                    block_wake_listening()
                                    rec.Reset()
        except Exception as e:
            print(f"\n[Voice Engine Error] Exception in audio input stream: {e}")
    else:
        while True:
            time.sleep(10)


# --- GUI OVERLAY ---
class AssistantFace(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint |  
            Qt.WindowType.Tool                     
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(300, 560)
        self.move(20, 20)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(10)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        self.face_container = QWidget()
        self.face_container.setFixedSize(240, 240)
        self.face_container_layout = QVBoxLayout(self.face_container)
        self.face_container_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_container_layout.addWidget(self.image_label)
        
        self.layout.addWidget(self.face_container)
        
        self.subtitle_label = QLabel()
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            "color: #F8F9FA; font-family: 'Outfit', 'Inter', sans-serif; font-size: 13px; "
            "font-weight: bold; background-color: rgba(20, 20, 20, 0.75); "
            "border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; padding: 6px;"
        )
        self.subtitle_label.setFixedWidth(280)
        self.subtitle_label.hide()
        self.layout.addWidget(self.subtitle_label)
        
        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.setFixedHeight(180)
        self.chat_log.setFixedWidth(280)
        self.chat_log.setStyleSheet(
            "background-color: rgba(15, 15, 15, 0.85); color: #00FFC8; "
            "border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; "
            "font-family: 'Courier New', monospace; font-size: 11px; padding: 6px;"
        )
        self.layout.addWidget(self.chat_log)
        
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type command...")
        self.chat_input.setFixedWidth(280)
        self.chat_input.setStyleSheet(
            "background-color: rgba(30, 30, 30, 0.90); color: #FFFFFF; "
            "border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 6px; "
            "font-family: 'Outfit', 'Inter', sans-serif; font-size: 12px; padding: 6px;"
        )
        self.chat_input.returnPressed.connect(self.submit_gui_chat)
        self.layout.addWidget(self.chat_input)
        
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        signals.state_changed.connect(self.update_face)
        signals.subtitle_changed.connect(self.update_subtitles)
        signals.chat_log_append.connect(self.append_to_chat_log)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.windowHandle()
            if window:
                window.startSystemMove()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            emergency_kill_switch(None, None)

    def update_subtitles(self, text):
        self.subtitle_label.setText(text)
        if text.strip():
            self.subtitle_label.show()
        else:
            self.subtitle_label.hide()

    def append_to_chat_log(self, sender, text):
        if sender == "User":
            self.chat_log.append(f"<span style='color: #888888;'>You:</span> {text}\n")
        else:
            self.chat_log.append(f"<span style='color: #00FFC8; font-weight: bold;'>AI:</span> {text}\n")
            
        scrollbar = self.chat_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def submit_gui_chat(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        self.chat_input.clear()
        threading.Thread(target=process_and_respond, args=(text,), daemon=True).start()

    def update_face(self, state):
        folder_path = os.path.join(self.base_dir, state)
        
        glow_styles = {
            "idle": "border: 2px solid rgba(255, 255, 255, 0.15); border-radius: 120px; background-color: transparent;",
            "listening": "border: 3px solid rgba(0, 255, 200, 0.85); border-radius: 120px; background-color: rgba(0, 255, 200, 0.03);",
            "processing": "border: 3px solid rgba(255, 160, 0, 0.85); border-radius: 120px; background-color: rgba(255, 160, 0, 0.03);",
            "speaking": "border: 3px solid rgba(0, 150, 255, 0.85); border-radius: 120px; background-color: rgba(0, 150, 255, 0.03);"
        }
        style = glow_styles.get(state, glow_styles["idle"])
        self.face_container.setStyleSheet(f"QWidget {{ {style} }}")
        
        if os.path.exists(folder_path):
            available_gifs = [f for f in os.listdir(folder_path) if f.endswith('.gif')]
            
            if available_gifs:
                chosen_gif = random.choice(available_gifs)
                full_path = os.path.join(folder_path, chosen_gif)
                
                if self.image_label.movie():
                    self.image_label.movie().stop()
                
                new_movie = QMovie(full_path)
                new_movie.setScaledSize(self.face_container.size()) 
                self.image_label.setMovie(new_movie)
                new_movie.start()


def terminal_state_logger(state):
    print(f"[Assistant State]: {state.upper()}")


def terminal_input_loop():
    """Read typed commands from stdin (used by terminal and both modes)."""
    while not system_ready:
        time.sleep(0.5)

    print("\n========================================================")
    print("      AI VOICE ASSISTANT - TERMINAL INPUT READY")
    print("========================================================")
    print(f" -> Language: {CONFIG.get('language', 'en')}")
    print(" -> Speak (wake word) OR type a command below.")
    print(" -> Type 'exit' or 'quit' to shut down.")
    print("========================================================\n")

    while True:
        try:
            user_text = input("You: ").strip()
            if not user_text:
                continue

            if user_text.lower() in ["exit", "quit"]:
                print("Shutting down voice assistant...")
                os.kill(os.getpid(), signal.SIGTERM)
                break

            print(f"[USER (typed)]: {user_text}")
            process_and_respond(user_text)
            signals.state_changed.emit("idle")

        except KeyboardInterrupt:
            print("\nShutting down voice assistant...")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass
            break
        except Exception as e:
            print(f"[Terminal Input Error]: {e}")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGUSR1, emergency_kill_switch)
    
    if "--consolidate" in sys.argv:
        ensure_memory_manager()
        if CONFIG.get("enable_memory") and memory_mgr:
            print("[MemoryManager] Triggering manual memory consolidation...")
            try:
                memory_mgr.consolidate_memories()
                print("[MemoryManager] Consolidation completed.")
            except Exception as e:
                print(f"[MemoryManager Error] Consolidation failed: {e}")
        else:
            print("[MemoryManager] Memory is disabled in configuration.")
        sys.exit(0)
        
    ai_thread = threading.Thread(target=real_cognitive_loop, daemon=True)
    ai_thread.start()

    mode = CONFIG.get("mode", "gui")

    if mode == "gui":
        app = QApplication(sys.argv)
        face = AssistantFace()
        face.show()
        sys.exit(app.exec())

    if mode == "both":
        # GUI window + typed commands in this terminal
        signals.state_changed.connect(terminal_state_logger)
        threading.Thread(target=terminal_input_loop, daemon=True).start()
        app = QApplication(sys.argv)
        face = AssistantFace()
        face.show()
        sys.exit(app.exec())

    # terminal-only
    signals.state_changed.connect(terminal_state_logger)
    terminal_input_loop()
