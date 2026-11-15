import sys
import json
import queue
import threading
import sounddevice as sd
import ollama
import soundfile as sf
from kokoro_onnx import Kokoro
from vosk import Model, KaldiRecognizer

# Initialize a thread-safe queue to hold raw audio bytes
audio_queue = queue.Queue()

def audio_callback(indata, frames, time, status):
    """
    This callback is called by sounddevice for each audio block.
    It instantly drops the raw bytes into the queue to prevent blocking the audio stream.
    """
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(bytes(indata))

def listening_daemon():
    """
    This function runs in a background thread. It initializes the model, 
    starts the microphone stream, and processes the audio for the wake word.
    """
    print("Loading Vosk acoustic model...")
    # This automatically looks for the folder named "model" in the current directory
    model = Model("model") 
    
    # Initialize the Kaldi Recognizer at 16,000 Hz
    rec = KaldiRecognizer(model, 16000)

    print("Initializing microphone stream...")
    # --- ADD THIS LINE TO LOAD THE VOICE ENGINE ---
    kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

    # Configure the raw input stream to match our required constraints
    stream = sd.RawInputStream(
        samplerate=16000, 
        blocksize=8000, 
        device=None,      # Uses system default microphone (PipeWire/ALSA)
        dtype='int16',    # 16-bit Signed Integer
        channels=1,       # Mono audio
        callback=audio_callback
    )

    with stream:
        print("Assistant is actively listening in the background...")
        while True:
            # Block until audio data is available in the queue
            data = audio_queue.get()
            
            # AcceptWaveform returns True when a period of silence is detected (sentence end)
            if rec.AcceptWaveform(data):
                # Extract the final parsed text from the Vosk JSON output
                result = json.loads(rec.Result())
                transcribed_text = result.get("text", "").lower()
                
                if transcribed_text:
                    # --- THE WAKE-WORD GATEWAY ---
                    wake_word = "hey assistant"
                    
                    if transcribed_text.startswith(wake_word):
                        # Strip the wake word out to get the actual command
                        command = transcribed_text.replace(wake_word, "").strip()
                        print(f"\n[WAKE WORD DETECTED]")
                        print(f"User Command: '{command}'")
                        
                        # --- TODO: Forward 'command' to your Ollama LLM logic here ---
                        if transcribed_text.startswith(wake_word):
                            # Strip the wake word out to get the actual command
                            command = transcribed_text.replace(wake_word, "").strip()
                            print(f"\n[WAKE WORD DETECTED]")
                            print(f"User Command: '{command}'")
                            
                            # --- THE COGNITIVE ROUTING ---
                            print("Thinking...")
                            
                            # Send the transcribed text strictly to our lightweight local model
                            response = ollama.chat(model='qwen2.5:1.5b', messages=[
                                {
                                    'role': 'system', 
                                    'content': 'You are a helpful, concise AI assistant. Keep answers brief.'
                                },
                                {
                                    'role': 'user', 
                                    'content': command
                                }
                            ])
                            
                            # Extract and print the generated response
                            ai_text = response['message']['content']
                            
                            # --- NEW VOCAL SYNTHESIS LOGIC ---
                            print("Speaking...")
                            # Generate the audio using a standard American female voice ('af_heart')
                            samples, sample_rate = kokoro.create(ai_text, voice="af_heart", speed=1.0, lang="en-us")
                            
                            # Play the audio out loud (this blocks the microphone temporarily while it speaks)
                            sd.play(samples, sample_rate)
                            sd.wait()
                            
                            # Give a slight delay for the hardware buffer to clear and clear any queued noise
                            import time
                            time.sleep(0.5)
                            while not audio_queue.empty():
                                try:
                                    audio_queue.get_nowait()
                                except queue.Empty:
                                    pass
                                    
                            print("\nListening...")
                        
                    else:
                        # Ignore background chatter that doesn't start with the wake word
                        pass 

if __name__ == "__main__":
    # Launch the listening daemon as a background thread
    # daemon=True ensures this thread dies automatically when the main program closes
    listener_thread = threading.Thread(target=listening_daemon, daemon=True)
    listener_thread.start()

    # Keep the main thread alive (In your final architecture, this is where 
    # your PySide6 GUI event loop would run instead)
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\nShutting down AI Assistant...")
        sys.exit(0)