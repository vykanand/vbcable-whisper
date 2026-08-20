import sys
import os
import time
import threading
import queue
import signal
import datetime
import struct
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

import numpy as np
import pyaudio
from faster_whisper import WhisperModel

stop_event = threading.Event()
ws_lock = threading.Lock()
ws_clients = set()
buffer_lock = threading.Lock()

ENERGY_THRESHOLD = 50
CHUNK_SIZE = 1024
RATE = 16000

model = None
topic_index = None

def load_model():
    global model
    print("Loading Whisper model...")
    model = WhisperModel('small', device='cpu', compute_type='int8')
    print("Model loaded")
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
                                'title': res.get('title', topic.get('title', ''))
                            })
    topic_index = index
    print("Topics loaded")
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

def send_line(line):
    if not line:
        return
    global ws_clients
    with ws_lock:
        dead = set()
        for c in list(ws_clients):
            try:
                c.send(f'data: {line}\n\n'.encode())
            except:
                dead.add(c)
        ws_clients -= dead

def audio_capture(stream, q):
    while not stop_event.is_set():
        try:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            samples = struct.unpack(f'{CHUNK_SIZE}h', data)
            q.put(samples)
        except:
            pass

def audio_process(model_ref, source, q):
    buf = []
    silence = 0
    first_chunk = True
    
    while not stop_event.is_set() or not q.empty():
        try:
            chunk = q.get(timeout=0.1)
        except queue.Empty:
            if silence > 30 and len(buf) >= 4800:
                seg = np.array(buf[:48000], dtype=np.float32) / 32768.0
                try:
                    segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                    txt = ' '.join(s.text for s in segments).strip()
                    if txt:
                        ts = datetime.datetime.now().strftime('%H:%M:%S')
                        send_line(f'[{ts}] [{source}]: "{txt}"')
                        if source == 'SYS':
                            for m in topic_match(txt):
                                send_line(f'[{ts}] [MATCH] {m["title"]} -> {m["url"]}')
                except Exception as e:
                    print(f"Transcribe error: {e}")
                buf = []
                silence = 0
            continue
        
        buf.extend(chunk)
        rms = float(np.sqrt(np.mean(np.array(chunk, dtype=np.float32)**2)))
        
        if first_chunk:
            silence = 0
            first_chunk = False
        
        if rms <= ENERGY_THRESHOLD:
            silence += 1
        else:
            silence = 0
        
        if silence > 32 and len(buf) >= 4800:
            seg = np.array(buf[:48000], dtype=np.float32) / 32768.0
            try:
                segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                txt = ' '.join(s.text for s in segments).strip()
                if txt:
                    ts = datetime.datetime.now().strftime('%H:%M:%S')
                    send_line(f'[{ts}] [{source}]: "{txt}"')
                    if source == 'SYS':
                        for m in topic_match(txt):
                            send_line(f'[{ts}] [MATCH] {m["title"]} -> {m["url"]}')
            except Exception as e:
                pass
            buf = []
            silence = 0
        elif len(buf) > 48000:
            seg = np.array(buf[:48000], dtype=np.float32) / 32768.0
            try:
                segments, _ = model_ref.transcribe(seg, language='en', beam_size=5, best_of=5, temperature=0.0)
                txt = ' '.join(s.text for s in segments).strip()
                if txt:
                    ts = datetime.datetime.now().strftime('%H:%M:%S')
                    send_line(f'[{ts}] [{source}]: "{txt}"')
            except:
                pass
            buf = buf[24000:]

HTML_PAGE = b'''<!DOCTYPE html>
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
</style></head>
<body><div class="container">
<h1>Live Transcription & Topic Matching</h1>
<div class="transcript" id="t"></div>
</div>
<script>
const t=document.getElementById('t');
const es=new EventSource('/ws');
es.onopen=function(){};
es.onmessage=function(e){
  const d=document.createElement('div');
  d.className='line';
  if(e.data.includes('[MIC]'))d.classList.add('mic');
  else if(e.data.includes('[SYS]'))d.classList.add('sys');
  else if(e.data.includes('[MATCH]'))d.classList.add('match');
  d.textContent=e.data;
  t.appendChild(d);
  t.scrollTop=t.scrollHeight;
};
es.onerror=function(){};
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', len(HTML_PAGE))
            self.end_headers()
            self.wfile.write(HTML_PAGE)
        elif self.path == '/ws':
            self.send_response(200)
            self.send_header('Content-type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            ws_lock.acquire()
            ws_clients.add(self)
            ws_lock.release()
            try:
                while not stop_event.is_set():
                    time.sleep(1)
            finally:
                ws_lock.acquire()
                ws_clients.discard(self)
                ws_lock.release()
        else:
            self.send_error(404)
    
    def log_message(self, *args):
        pass

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), Handler)
    print("Server on http://localhost:8080")
    server.serve_forever()

def find_devices():
    p = pyaudio.PyAudio()
    n = p.get_host_api_info_by_index(0).get('deviceCount', 0)
    mic, sys = 1, 3
    for i in range(n):
        d = p.get_device_info_by_index(i)
        name = (d.get('name') or '').lower()
        mi = d.get('maxInputChannels', 0)
        if 'usb enc' in name and mi > 0:
            mic = i
        if 'cable output' in name and mi > 0:
            sys = i
    p.terminate()
    return mic, sys

def main():
    mic_idx, sys_idx = find_devices()
    print(f"Devices: MIC={mic_idx}, SYS={sys_idx}")
    
    p = pyaudio.PyAudio()
    
    mic_stream = p.open(
        format=pyaudio.paInt16, channels=1, rate=RATE,
        input=True, input_device_index=mic_idx, frames_per_buffer=CHUNK_SIZE
    )
    sys_stream = p.open(
        format=pyaudio.paInt16, channels=1, rate=RATE,
        input=True, input_device_index=sys_idx, frames_per_buffer=CHUNK_SIZE
    )
    
    mic_q = queue.Queue()
    sys_q = queue.Queue()
    
    threading.Thread(target=audio_capture, args=(mic_stream, mic_q), daemon=True).start()
    threading.Thread(target=audio_capture, args=(sys_stream, sys_q), daemon=True).start()
    
    print("Loading model (this may take 30-60s for first run)...")
    load_model()
    
    print("Loading topics...")
    load_topics()
    
    print("Starting transcription workers...")
    threading.Thread(target=audio_process, args=(model, 'MIC', mic_q), daemon=True).start()
    threading.Thread(target=audio_process, args=(model, 'SYS', sys_q), daemon=True).start()
    
    signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
    signal.signal(signal.SIGTERM, lambda s, f: stop_event.set())
    
    try:
        run_server()
    finally:
        stop_event.set()
        mic_stream.stop_stream()
        mic_stream.close()
        sys_stream.stop_stream()
        sys_stream.close()
        p.terminate()
        print("\nServer stopped")

if __name__ == '__main__':
    main()