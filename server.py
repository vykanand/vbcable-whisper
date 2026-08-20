import sys
import os
import time
import threading
import queue
import signal
import datetime
import struct
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from collections import defaultdict

import numpy as np
import pyaudio
from faster_whisper import WhisperModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

stop_event = threading.Event()
ws_lock = threading.Lock()
ws_clients = set()

# Thread-safe queue for passing transcript lines to SSE handler
transcript_queue = queue.Queue()

ENERGY_THRESHOLD = 50
CHUNK_SIZE = 1024
RATE = 16000

model = None
topic_index = None

def load_model():
    global model
    logger.info("Loading Whisper model...")
    model = WhisperModel('small', device='cpu', compute_type='int8')
    logger.info("Model loaded")
    return model

def load_topics():
    global topic_index
    with open('topics.json', 'r') as f:
        data = json.load(f)
    index = defaultdict(list)
    for cat in data.get('categories', []):
        for topic in cat.get('topics', []):
            concepts = ' '.join(topic.get('concepts', [])).lower()
            title = topic.get('title', '').lower()
            text = concepts + ' ' + title
            for res in topic.get('resources', []):
                url = res.get('url', '')
                if url and not url.startswith('/'):
                    for word in text.split():
                        if len(word) > 2:
                            index[word].append({
                                'url': url,
                                'title': res.get('title', topic.get('title'))
                            })
    topic_index = index
    logger.info("Topics loaded")
    return index

def topic_match(text):
    if not topic_index:
        return []
    words = set(text.lower().split())
    results = []
    seen = set()
    for w in words:
        for item in topic_index.get(w, []):
            if item['url'] not in seen:
                seen.add(item['url'])
                results.append(item)
    return results[:5]

def push_transcript(line):
    """Add a transcript line to the queue for SSE broadcasting"""
    if not line:
        return
    global ws_clients
    with ws_lock:
        dead = set()
        for c in list(ws_clients):
            try:
                c.wfile.write(f'data: {line}\n\n'.encode())
                c.wfile.flush()
            except:
                dead.add(c)
        ws_clients -= dead

def audio_capture(stream, q):
    logger.info("Audio capture thread started")
    while not stop_event.is_set():
        try:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            samples = struct.unpack(f"{len(data)//2}h", data)
            q.put_nowait(samples)
        except Exception as e:
            if not stop_event.is_set():
                logger.error(f"Audio capture error: {e}")

def audio_process(model_ref, source, q):
    logger.info(f"Audio process started for {source}")
    buffer = []
    speech_active = False
    silence_counter = 0
    
    SILENCE_THRESHOLD = 32
    MIN_BUFFER_SAMPLES = 4800
    MAX_BUFFER_SAMPLES = 48000
    
    while not stop_event.is_set() or not q.empty():
        try:
            chunk = q.get(timeout=0.1)
        except queue.Empty:
            if speech_active and len(buffer) >= MIN_BUFFER_SAMPLES:
                seg = np.array(buffer[:MAX_BUFFER_SAMPLES], dtype=np.float32) / 32768.0
                try:
                    segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                    txt = ' '.join(s.text for s in segments).strip()
                    if txt:
                        ts = datetime.datetime.now().strftime('%H:%M:%S')
                        line = f'[{ts}] [{source}]: "{txt}"'
                        push_transcript(line)
                        logger.info(f"Transcribed [{source}]: {txt[:80]}")
                        if source == 'SYS':
                            for m in topic_match(txt):
                                push_transcript(f'[{ts}] [MATCH] {m["title"]} -> {m["url"]}')
                except Exception as e:
                    logger.error(f"Transcribe error: {e}")
                buffer = []
                speech_active = False
                silence_counter = 0
            continue
        
        chunk_rms = float(np.sqrt(np.mean(np.array(chunk, dtype=np.float32) ** 2))) if chunk else 0.0
        
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
                    try:
                        segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                        txt = ' '.join(s.text for s in segments).strip()
                        if txt:
                            ts = datetime.datetime.now().strftime('%H:%M:%S')
                            line = f'[{ts}] [{source}]: "{txt}"'
                            push_transcript(line)
                            logger.info(f"Transcribed [{source}]: {txt[:80]}")
                            if source == 'SYS':
                                for m in topic_match(txt):
                                    push_transcript(f'[{ts}] [MATCH] {m["title"]} -> {m["url"]}')
                    except Exception as e:
                        logger.error(f"Transcribe error: {e}")
                buffer = []
                speech_active = False
                silence_counter = 0
            elif len(buffer) >= MAX_BUFFER_SAMPLES:
                seg = np.array(buffer[:MAX_BUFFER_SAMPLES], dtype=np.float32) / 32768.0
                try:
                    segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                    txt = ' '.join(s.text for s in segments).strip()
                    if txt:
                        ts = datetime.datetime.now().strftime('%H:%M:%S')
                        line = f'[{ts}] [{source}]: "{txt}"'
                        push_transcript(line)
                        logger.info(f"Transcribed [{source}]: {txt[:80]}")
                except Exception as e:
                    logger.error(f"Transcribe error: {e}")
                buffer = buffer[MAX_BUFFER_SAMPLES:]
                silence_counter = 0

HTML = b'''<!DOCTYPE html>
<html><head><title>Live Transcription</title>
<style>
body{font-family:Arial;margin:20px;background:#f5f5f5}
.container{max-width:1200px;margin:0 auto}
h1{color:#333;margin-bottom:20px}
.transcript{background:#fff;padding:20px;border-radius:8px;height:400px;overflow-y:auto;box-shadow:0 2px 4px rgba(0,0,0,0.1)}
.line{padding:5px 0;border-bottom:1px solid #eee;font-family:monospace;font-size:14px}
.mic{color:#2196F3}
.sys{color:#4CAF50;font-weight:bold}
.match{color:#FF9800;font-size:13px}
.log{color:#999;font-size:12px}
</style></head>
<body><div class="container">
<h1>Live Transcription & Topic Matching</h1>
<div id="status" class="log">Connecting...</div>
<div class="transcript" id="t"></div>
</div>
<script>
const t=document.getElementById('t');
const s=document.getElementById('status');
const es=new EventSource('/ws');
es.onopen=function(){s.textContent='Connected - Waiting for audio...';};
es.onmessage=function(e){
  s.textContent='Live';
  const d=document.createElement('div');
  d.className='line';
  if(e.data.includes('[MIC]'))d.classList.add('mic');
  else if(e.data.includes('[SYS]'))d.classList.add('sys');
  else if(e.data.includes('[MATCH]'))d.classList.add('match');
  d.textContent=e.data;
  t.appendChild(d);
  t.scrollTop=t.scrollHeight;
};
es.onerror=function(){s.textContent='Disconnected';};
</script></body></html>'''

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', len(HTML))
            self.end_headers()
            self.wfile.write(HTML)
            logger.info(f"Served HTML page to {self.client_address[0]}")
        elif self.path == '/ws':
            self.send_response(200)
            self.send_header('Content-type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            ws_lock.acquire()
            ws_clients.add(self)
            logger.info(f"SSE client connected: {self.client_address[0]} (total: {len(ws_clients)})")
            # Send any pending transcript lines on connect
            while not transcript_queue.empty():
                try:
                    line = transcript_queue.get_nowait()
                    self.wfile.write(f'data: {line}\n\n'.encode())
                    self.wfile.flush()
                except:
                    pass
            ws_lock.release()
            # Keep connection alive and forward new transcript lines
            try:
                while not stop_event.is_set():
                    try:
                        line = transcript_queue.get(timeout=0.5)
                        self.wfile.write(f'data: {line}\n\n'.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        pass
            except:
                pass
        else:
            self.send_error(404)
    
    def log_message(self, *args):
        pass

def run_server():
    server = ThreadedHTTPServer(('0.0.0.0', 8080), Handler)
    logger.info("Server started on http://localhost:8080")
    server.serve_forever()

def find_devices():
    p = pyaudio.PyAudio()
    n = p.get_host_api_info_by_index(0).get('deviceCount', 0)
    mic_idx = sys_idx = None
    for i in range(n):
        d = p.get_device_info_by_index(i)
        nm = (d.get('name') or '').lower()
        mi = d.get('maxInputChannels', 0)
        if mic_idx is None and mi > 0 and ("usb enc" in nm or "enc audio" in nm or "headset" in nm):
            mic_idx = i
        if sys_idx is None and mi > 0 and "cable output" in nm:
            sys_idx = i
    if sys_idx is None:
        for i in range(n):
            d = p.get_device_info_by_index(i)
            nm = (d.get('name') or '').lower()
            if d.get('maxInputChannels', 0) > 0 and ("stereo mix" in nm or "loopback" in nm or "what u hear" in nm):
                sys_idx = i
                break
    p.terminate()
    if mic_idx is None:
        mic_idx = 1
    if sys_idx is None:
        sys_idx = 3
    p2 = pyaudio.PyAudio()
    mic_name = p2.get_device_info_by_index(mic_idx).get('name', '?')
    sys_name = p2.get_device_info_by_index(sys_idx).get('name', '?')
    p2.terminate()
    logger.info(f"Selected devices: MIC={mic_idx} ({mic_name}), SYS={sys_idx} ({sys_name})")
    return mic_idx, sys_idx

def main():
    global model, topic_index
    
    mic_idx, sys_idx = find_devices()
    logger.info(f"Using devices: MIC={mic_idx}, SYS={sys_idx}")
    
    p = pyaudio.PyAudio()
    mic_stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                       input_device_index=mic_idx, frames_per_buffer=CHUNK_SIZE)
    sys_stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                       input_device_index=sys_idx, frames_per_buffer=CHUNK_SIZE)
    
    mic_q = queue.Queue()
    sys_q = queue.Queue()
    
    threading.Thread(target=audio_capture, args=(mic_stream, mic_q), daemon=True).start()
    threading.Thread(target=audio_capture, args=(sys_stream, sys_q), daemon=True).start()
    
    logger.info("Loading model...")
    model = load_model()
    
    logger.info("Loading topics...")
    topic_index = load_topics()
    
    logger.info("Starting audio workers...")
    threading.Thread(target=audio_process, args=(model, 'MIC', mic_q), daemon=True).start()
    threading.Thread(target=audio_process, args=(model, 'SYS', sys_q), daemon=True).start()
    
    logger.info("Starting web server...\nOpen http://localhost:8080")
    
    signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
    signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())
    
    try:
        run_server()
    finally:
        logger.info("Shutting down...")
        stop_event.set()
        mic_stream.stop_stream()
        mic_stream.close()
        sys_stream.stop_stream()
        sys_stream.close()
        p.terminate()
        logger.info("Server stopped")

if __name__ == '__main__':
    main()