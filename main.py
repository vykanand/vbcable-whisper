import sys
import time
import threading
import queue
import signal
import datetime
import struct

import numpy as np
import pyaudio
from faster_whisper import WhisperModel

stop_event = threading.Event()
file_lock = threading.Lock()

ENERGY_THRESHOLD = 50
CHUNK_SIZE = 1024
RATE = 16000
SHOW_LEVELS = True

def list_audio_devices():
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    num_devices = info.get("deviceCount")
    print("\n=== Available Audio Devices ===")
    for i in range(num_devices):
        dev = p.get_device_info_by_index(i)
        name = dev.get("name")
        max_in = dev.get("maxInputChannels")
        max_out = dev.get("maxOutputChannels")
        host = dev.get("hostApi")
        print(f"[{i}] {name} | Input: {max_in} | Output: {max_out} | Host: {host}")
    p.terminate()
    print()

def select_device(prompt, min_channels=1):
    while True:
        try:
            idx_str = input(prompt).strip()
            idx = int(idx_str)
        except (ValueError, EOFError):
            print("Please enter a valid integer.")
            continue
        p = pyaudio.PyAudio()
        try:
            dev = p.get_device_info_by_index(idx)
        except Exception:
            p.terminate()
            print("Device index out of range. Try again.")
            continue
        p.terminate()
        if dev.get("maxInputChannels", 0) >= min_channels or dev.get("maxOutputChannels", 0) >= min_channels:
            return idx
        print(f"Device {idx} does not have enough channels. Try again.")

def auto_pick_devices():
    p = pyaudio.PyAudio()
    n = p.get_host_api_info_by_index(0).get("deviceCount")
    mic_idx = sys_idx = None
    for i in range(n):
        d = p.get_device_info_by_index(i)
        nm = (d.get("name") or "").lower()
        mi = d.get("maxInputChannels", 0)
        if mic_idx is None and mi > 0 and ("usb enc" in nm or "enc audio" in nm or "headset" in nm):
            mic_idx = i
        if sys_idx is None and mi > 0:
            if "cable output" in nm:
                sys_idx = i
    if sys_idx is None:
        for i in range(n):
            d = p.get_device_info_by_index(i)
            nm = (d.get("name") or "").lower()
            if d.get("maxInputChannels", 0) > 0 and ("stereo mix" in nm or "loopback" in nm or "what u hear" in nm):
                sys_idx = i
                break
    p.terminate()
    return mic_idx, sys_idx

def capture_stream(stream, target_queue):
    while not stop_event.is_set():
        try:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            samples = struct.unpack(f"{len(data)//2}h", data)
            target_queue.put_nowait(samples)
        except Exception as e:
            if not stop_event.is_set():
                print(f"\n[ERROR] capture stream: {e}", flush=True)
            break

def output_cb(line, file_handle):
    print(line, flush=True)
    with file_lock:
        try:
            file_handle.write(line + "\n")
            file_handle.flush()
        except Exception:
            pass

def transcribe_segment(audio_array, output_cb, file_handle, source, model):
    try:
        segments, _ = model.transcribe(
            audio_array,
            language="en",
            beam_size=5,
            best_of=5,
            temperature=0.0,
        )
        text = " ".join(s.text for s in segments).strip()
        if text:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            output_cb(f'[{ts}] [{source}]: "{text}"', file_handle)
    except Exception as ex:
        print(f"\n[ERROR] Transcribe: {ex}", flush=True)

def process_audio(model, source, audio_queue, output_cb, file_handle):
    buffer = []
    speech_active = False
    silence_counter = 0
    
    SILENCE_THRESHOLD = 32
    MIN_BUFFER_SAMPLES = 4800
    MAX_BUFFER_SAMPLES = 48000
    
    while not stop_event.is_set() or not audio_queue.empty():
        try:
            chunk = audio_queue.get(timeout=0.1)
        except queue.Empty:
            if speech_active and len(buffer) >= MIN_BUFFER_SAMPLES:
                seg = np.array(buffer[:MAX_BUFFER_SAMPLES], dtype=np.float32) / 32768.0
                transcribe_segment(seg, output_cb, file_handle, source, model)
                buffer = []
                speech_active = False
                silence_counter = 0
            continue
        
        chunk_rms = np.sqrt(np.mean(np.array(chunk, dtype=np.float32) ** 2)) if chunk else 0.0
        
        if SHOW_LEVELS:
            bar = "#" * int(min(chunk_rms / 50.0, 1.0) * 30)
            print(f"\r[{source}] RMS={chunk_rms:6.1f} |{bar:<30}|", end="", flush=True)
        
        if not speech_active:
            if chunk_rms > ENERGY_THRESHOLD:
                speech_active = True
                silence_counter = 0
                buffer = list(chunk)
        else:
            buffer.extend(chunk)
            
            if chunk_rms > ENERGY_THRESHOLD:
                silence_counter = 0
            else:
                silence_counter += 1
            
            if silence_counter >= SILENCE_THRESHOLD:
                if len(buffer) >= MIN_BUFFER_SAMPLES:
                    seg_len = min(len(buffer), MAX_BUFFER_SAMPLES)
                    seg = np.array(buffer[:seg_len], dtype=np.float32) / 32768.0
                    transcribe_segment(seg, output_cb, file_handle, source, model)
                buffer = []
                speech_active = False
                silence_counter = 0
            elif len(buffer) >= MAX_BUFFER_SAMPLES:
                seg = np.array(buffer[:MAX_BUFFER_SAMPLES], dtype=np.float32) / 32768.0
                transcribe_segment(seg, output_cb, file_handle, source, model)
                buffer = buffer[MAX_BUFFER_SAMPLES:]
                silence_counter = 0

def main():
    list_audio_devices()
    
    manual = "--manual" in sys.argv
    mic_idx, sys_idx = (None, None) if manual else auto_pick_devices()
    
    if mic_idx is not None:
        print(f"[AUTO] Microphone  -> device {mic_idx}")
    else:
        mic_idx = select_device("Enter the index ID for your Microphone: ")
    
    if sys_idx is not None:
        print(f"[AUTO] Speaker/Loopback -> device {sys_idx}")
    else:
        sys_idx = select_device("Enter the index ID for your Speaker/Loopback device: ")
    
    print()
    
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    
    p = pyaudio.PyAudio()
    
    try:
        mic_stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
                           input_device_index=mic_idx, frames_per_buffer=CHUNK_SIZE)
        sys_stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
                           input_device_index=sys_idx, frames_per_buffer=CHUNK_SIZE)
    except Exception as e:
        p.terminate()
        print(f"\n[FATAL] Could not open audio streams: {e}", flush=True)
        sys.exit(1)
    
    mic_queue = queue.Queue()
    sys_queue = queue.Queue()
    
    mic_thread = threading.Thread(target=capture_stream, args=(mic_stream, mic_queue), daemon=True)
    sys_thread = threading.Thread(target=capture_stream, args=(sys_stream, sys_queue), daemon=True)
    
    mic_thread.start()
    sys_thread.start()
    
    model_name = "small"
    if "--tiny" in sys.argv:
        model_name = "tiny"
    elif "--base" in sys.argv:
        model_name = "base"
    elif "--medium" in sys.argv:
        model_name = "medium"
    elif "--large-v3" in sys.argv:
        model_name = "large-v3"
    print(f"\nLoading Whisper '{model_name}' model (first run may download/cache)...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    
    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"call_transcript_{ts_str}.md"
    fhandle = open(filename, "w", encoding="utf-8")
    fhandle.write(f"# Call Transcript – {datetime.datetime.now().isoformat()}\n\n")
    
    mic_worker = threading.Thread(
        target=process_audio, 
        args=(model, "MIC", mic_queue, output_cb, fhandle), 
        daemon=True
    )
    sys_worker = threading.Thread(
        target=process_audio, 
        args=(model, "SYS", sys_queue, output_cb, fhandle), 
        daemon=True
    )
    
    mic_worker.start()
    sys_worker.start()
    
    print("Model loaded. Workers started.")
    print("\n--- Starting real-time transcription (Ctrl+C to stop) ---\n")
    
    def signal_handler(sig, frame):
        raise KeyboardInterrupt()
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        mic_thread.join(timeout=2.0)
        sys_thread.join(timeout=2.0)
        mic_worker.join(timeout=2.0)
        sys_worker.join(timeout=2.0)
        mic_stream.stop_stream()
        mic_stream.close()
        sys_stream.stop_stream()
        sys_stream.close()
        p.terminate()
        fhandle.close()
        print(f"\nStreams closed. Transcript saved to {filename}")

if __name__ == "__main__":
    main()