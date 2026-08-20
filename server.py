import sys
import os
import re
import time
import threading
import queue
import signal
import datetime
import struct
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote, unquote
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

# ----- keep-ai compatible retrieval (same-to-same as keep-ai) -----
keep_index = None          # data/index.json  {chunks, stats}
keep_catalogue = []        # data/catalogue.json
KEEP_DIR = os.environ.get('KEEP_DIR', 'keep-inbox')
KEEP_NOTE_BASE = 'http://localhost:8080/note/'
NOTE_CACHE_SECONDS = 30
note_cache = {}            # note_id -> last emitted time
url_cache = {}             # url -> last emitted time
_k1 = 1.5
_b = 0.75
KEEP_STOPWORDS = set(('a an and are as at be been being but by can could did do does for from had has have how i if in into is it its may might must nor not of on or should so that the their them then there these they this those to was we were what when which who will with would you your most such than too very just also').split(' '))

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

def topic_word_matches(text):
    if not topic_index:
        return []
    words = {w for w in re.findall(r'[a-z0-9]+', text.lower()) if len(w) > 2}
    results = []
    seen = set()
    for w in words:
        for item in topic_index.get(w, []):
            key = (w, item['url'])
            if key not in seen:
                seen.add(key)
                results.append({'word': w, 'title': item['title'], 'url': item['url']})
    return results

# ===== emitters =====

# ===== keep-ai exact retrieval (ported from lib/retrieval.js + lib/chat.js) =====

def keep_tokenize(text):
    return [w for w in re.sub(r'[\u0000-\u002f\u003a-\u0040\u005b-\u0060\u007b-\u00bf]', ' ', (text or '').lower()).split() if len(w) > 1]

def keep_trigrams(word):
    return {word[i:i+3] for i in range(len(word) - 2)}

def keep_term_match(q, t):
    if q == t: return 1.0
    if t.startswith(q): return 0.85
    if q.startswith(t): return 0.7
    if t.endswith(q): return 0.55
    if q.endswith(t): return 0.45
    return 0

def keep_best_title_hit(q, title_terms):
    best = 0.0
    for t in title_terms:
        m = keep_term_match(q, t)
        if m > best: best = m
    return best

def keep_overlap(a, b):
    return sum(1 for g in a if g in b)

def keep_title_trigram_hit(q_trigs, title_terms):
    best = 0.0
    for t in title_terms:
        if len(t) < 4: continue
        ov = keep_overlap(q_trigs, keep_trigrams(t))
        if ov >= 3: return 0.55
        if ov == 2 and ov > best: best = 0.35
    return best

def keep_score_chunk(c, stats, q_terms):
    s = 0.0
    seen = set()
    for w in q_terms:
        if w in seen: continue
        seen.add(w)
        idf = stats.get('idf', {}).get(w)
        if not idf or w not in c.get('terms', {}): continue
        tf = c['terms'][w]
        norm = 1 - _b + _b * (c.get('dl', 0) / (stats.get('avgdl') or 1))
        s += (idf * (tf * (_k1 + 1))) / (tf + _k1 * norm)
    return s

def keep_score_chunk_smart(c, stats, q_terms, q_info, title_boost):
    s = keep_score_chunk(c, stats, q_terms)
    if not title_boost: return s
    for q in q_info:
        widf = 0.6 + (stats.get('idf', {}).get(q['word']) or 0)
        hit = keep_best_title_hit(q['word'], c.get('titleTerms', {}))
        if hit > 0: s += title_boost * hit * widf
        if len(q['word']) >= 4 and q['trigs']:
            s += title_boost * keep_title_trigram_hit(q['trigs'], c.get('titleTerms', {})) * (0.25 + 0.4 * (stats.get('idf', {}).get(q['word']) or 0))
    return s

def keep_search(index, query, k=8, title_boost=4):
    raw = keep_tokenize(query)
    q_terms = [w for w in raw if w not in KEEP_STOPWORDS]
    if not q_terms:
        q_terms = raw
    if not q_terms:
        return []
    q_info = [{'word': w, 'trigs': keep_trigrams(w) if len(w) >= 4 else None} for w in q_terms]
    stats = index.get('stats', {})
    scored = [{'chunk': c, 'score': keep_score_chunk_smart(c, stats, q_terms, q_info, title_boost)} for c in index.get('chunks', [])]
    scored.sort(key=lambda x: x['score'], reverse=True)
    out = []
    for s in scored:
        if s['score'] > 0:
            c = dict(s['chunk'])
            c['score'] = round(s['score'], 4)
            out.append(c)
            if len(out) >= k:
                break
    return out

def keep_related_results(query, k=20):
    if not keep_index or not keep_index.get('chunks'):
        return []
    if not keep_catalogue:
        return []
    q_words = [w for w in re.split(r'[^a-z0-9]+', (query or '').lower()) if len(w) > 2]
    hits = keep_search(keep_index, query, 120, 4)
    best = {}
    for h in hits:
        s = h.get('score') or 0
        cur = best.get(h['id'])
        title_l = (h.get('title') or '').lower()
        title_hit = sum(1 for w in q_words if w in title_l)
        if not cur or s > cur['score']:
            best[h['id']] = {'score': s, 'hits': ((cur['hits'] if cur else 0) + 1), 'title_hit': title_hit}
        elif cur:
            cur['hits'] += 1
            if title_hit > cur['title_hit']:
                cur['title_hit'] = title_hit
    if not best:
        return []
    by_id = {e['id']: e for e in keep_catalogue}
    out = []
    for nid, v in best.items():
        e = by_id.get(nid)
        if not e:
            continue
        score = round(v['score'] + 0.08 * min(v['hits'], 25) + 1.5 * min(v['title_hit'] or 0, 4), 4)
        out.append({
            'id': e.get('id'), 'kind': e.get('kind'), 'title': e.get('title', ''),
            'relPath': e.get('relPath', ''), 'summary': e.get('summary') or '',
            'text': e.get('text') or '', 'score': score,
            'labels': e.get('labels') or [], 'images': e.get('images') or [],
        })
    out.sort(key=lambda x: x['score'], reverse=True)
    return out[:k]

def load_keep_data():
    global keep_index, keep_catalogue
    index_file = os.path.join('data', 'index.json')
    cat_file = os.path.join('data', 'catalogue.json')
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                keep_index = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load keep-ai index: {e}")
            keep_index = None
    if os.path.exists(cat_file):
        try:
            with open(cat_file, 'r', encoding='utf-8') as f:
                keep_catalogue = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load keep-ai catalogue: {e}")
            keep_catalogue = []
    cnt = len(keep_index.get('chunks')) if keep_index and keep_index.get('chunks') else 0
    logger.info(f"KeepAI data loaded: {cnt} chunks, {len(keep_catalogue)} notes ({KEEP_DIR})")

def emit_keep_notes(source, ts, txt):
    """Emit Relevant Notes + KEEP-AI matching topics/links (keep-ai retrieval)."""
    if not keep_index:
        return
    try:
        rel = keep_related_results(txt, 5)
    except Exception as e:
        logger.error(f"KeepAI match error: {e}")
        return
    now = time.time()
    fresh = []
    for r in rel:
        last = note_cache.get(r['id'], 0)
        if now - last >= NOTE_CACHE_SECONDS:
            note_cache[r['id']] = now
            fresh.append(r)
    if not fresh:
        return
    push_transcript(f'[{ts}] [NOTES] Relevant Notes (keep-ai):')
    q_words = [w for w in re.split(r'[^a-z0-9]+', (txt or '').lower()) if len(w) > 2]
    for r in fresh:
        title = (r.get('title') or '').replace('\r', ' ').replace('\n', ' ')
        push_transcript(f'[{ts}] [NOTES] {title} (score {r["score"]})')
        title_l = title.lower()
        mw = [w for w in q_words if w in title_l]
        words = ', '.join(mw) if mw else title[:30]
        push_transcript(f'[{ts}] [KEEP-AI-W] {words} -> {KEEP_NOTE_BASE}{r["id"]}')
    push_transcript(f'[{ts}] [KEEP-AI] matching topics & links:')
    for r in fresh:
        title = (r.get('title') or '').replace('\r', ' ').replace('\n', ' ')
        push_transcript(f'[{ts}] [KEEP-AI] {title} -> {KEEP_NOTE_BASE}{r["id"]}')

def emit_topic_words(ts, txt):
    matches = topic_word_matches(txt)
    if not matches:
        return
    now = time.time()
    out = []
    seen = set()
    for m in matches:
        key = m['url']
        if now - url_cache.get(key, 0) >= NOTE_CACHE_SECONDS:
            url_cache[key] = now
            out.append(m)
            seen.add(key)
    if not out:
        return
    push_transcript(f'[{ts}] [MEDIUM ARTICLES] matching words & links:')
    for m in out[:8]:
        push_transcript(f'[{ts}] [MEDIUM ARTICLES] {m["word"]} -> {m["url"]}')

def after_transcribe(source, ts, txt):
    txt_clean = txt.replace('\r', ' ').replace('\n', ' ')
    push_transcript(f'[{ts}] [{source}]: "{txt_clean}"')
    logger.info(f"Transcribed [{source}]: {txt_clean[:80]}")
    if source == 'SYS':
        for m in topic_match(txt):
            push_transcript(f'[{ts}] [MATCH] {m["title"]} -> {m["url"]}')
    emit_keep_notes(source, ts, txt_clean)
    emit_topic_words(ts, txt_clean)

def push_transcript(line):
    """Add a transcript line to the queue for SSE broadcasting"""
    if not line:
        return
    global ws_clients
    line = line.replace('\r', ' ').replace('\n', ' ')
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
                        after_transcribe(source, ts, txt)
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
                            after_transcribe(source, ts, txt)
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
                        after_transcribe(source, ts, txt)
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
.match a,.notes a{color:#2196F3;text-decoration:underline;cursor:pointer}
.topics a{color:#FF9800;text-decoration:underline;cursor:pointer}
.keepai a,.keepaiw a{color:#4CAF50;text-decoration:underline;cursor:pointer}
.notes{color:#2196F3;font-size:13px}
.match,.topics{color:#FF9800;font-size:13px}
.keepai,.keepaiw{color:#4CAF50;font-size:13px;font-weight:bold}
.log{color:#999;font-size:12px}
.sechead{color:#333;font-weight:bold;font-size:13px;padding-top:4px}
 </style></head>
<body><div class="container">
<h1>Live Transcription & Topic Matching</h1>
<div class="log" style="margin-bottom:8px"><a href="/notes" style="color:#0a66c2">Browse Knowledge Base (/notes)  keep-ai index from data/</a></div>
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
   else if(e.data.includes('[MATCH]')||e.data.includes('[NOTES]')||e.data.includes('[KEEP-AI]')||e.data.includes('[MEDIUM ARTICLES]')||e.data.includes('[KEEP-AI-W]')){
     if(e.data.includes('[NOTES]')&&e.data.includes('Relevant Notes'))d.classList.add('sechead');
     else if(e.data.includes('[KEEP-AI]')&&e.data.includes('matching topics'))d.classList.add('sechead');
     else if(e.data.includes('[MEDIUM ARTICLES]')&&e.data.includes('matching words'))d.classList.add('sechead');
     else{
       if(e.data.includes('[MATCH]')||e.data.includes('[MEDIUM ARTICLES]'))d.classList.add('topics');
       else if(e.data.includes('[NOTES]'))d.classList.add('notes');
       else if(e.data.includes('[KEEP-AI')||e.data.includes('[KEEP-AI-W]'))d.classList.add('keepai');
     }
     const i=e.data.lastIndexOf('->');
     if(i>=0){
       const a=document.createElement('a');
       a.href=e.data.slice(i+2).trim();
       a.target='_blank';
       a.rel='noopener';
       a.textContent=e.data;
       d.appendChild(a);
       t.appendChild(d);
       t.scrollTop=t.scrollHeight;
       return;
     }
   }
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
        elif self.path == '/notes' or self.path == '/notes/':
            page = render_notes_index()
            body = page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith('/note/'):
            nid = unquote(self.path[len('/note/'):])
            page = render_note(nid)
            if page is None:
                self.send_error(404, 'Note not found.')
            else:
                body = page.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
        elif self.path.startswith('/keep-files/'):
            rel = unquote(self.path[len('/keep-files/'):]).split('?', 1)[0]
            full = os.path.normpath(os.path.join(KEEP_DIR, rel))
            root = os.path.normpath(KEEP_DIR)
            if not (full == root or full.startswith(root + os.sep)):
                self.send_error(403)
                return
            if os.path.isfile(full):
                ext = os.path.splitext(full)[1].lower()
                ctype = {'.html': 'text/html', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}.get(ext, 'application/octet-stream')
                try:
                    with open(full, 'rb') as f:
                        data = f.read()
                except Exception:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-type', ctype)
                self.send_header('Content-Length', len(data))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
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

def es(c):
    return str('' if c is None else c).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

def render_notes_index():
    items = sorted(keep_catalogue or [], key=lambda e: (e.get('title') or '').lower())
    rows = ''.join(
        '<li><a href="/note/' + e.get('id','') + '">' + es(e.get('title') or '(untitled)') + '</a>'
        + (' <span class="tag">' + es(e.get('kind') or 'note') + '</span>' if e.get('kind') else '')
        + '</li>' for e in items
    )
    return '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Notes · KeepAI</title>\n<style>:root{--bg:#f7f9fc;--border:#d3dbe6;--text:#1c2733;--accent:#0a66c2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}.wrap{max-width:760px;margin:0 auto;padding:28px 20px}.bar{display:flex;gap:10px;margin-bottom:20px}.bar a{color:var(--accent);text-decoration:none;font-size:13px}.tag{font-size:11px;color:#5b6b7c;border:1px solid var(--border);border-radius:999px;padding:2px 9px}#q{width:100%;padding:10px 14px;border:1px solid var(--border);border-radius:10px;font-size:14px}ul{list-style:none;padding:0;margin:0;border-top:1px solid var(--border)}li{padding:8px 4px;border-bottom:1px solid var(--border)}li a{color:var(--text);text-decoration:none}li a:hover{color:var(--accent);text-decoration:underline}</style></head><body><div class="wrap"><div class="bar"><a href="/">&#8592; Back</a></div><h1>Keep-AI Notes ({len(items)})</h1><input id="q" placeholder="Filter notes by title..." onkeyup="loc(this.value)"><ul id="l">'+rows+'</ul><script>var tags=document.getElementById("l").getElementsByTagName("li");function loc(v){v=(v||"").toLowerCase();for(var i=0;i<tags.length;i++){var t=tags[i].textContent.toLowerCase();tags[i].style.display=t.indexOf(v)>=0?"":"none";}}</script></body></html>'

def render_note(nid):
    entry = None
    for e in keep_catalogue:
        if e.get('id') == nid:
            entry = e
            break
    if not entry:
        return None
    title = entry.get('title') or '(untitled)'
    is_image = entry.get('kind') == 'image'
    body = (entry.get('text') or '').strip() or entry.get('summary') or ''
    labels = entry.get('labels') or []
    if not isinstance(labels, list):
        labels = []
    rel = entry.get('relPath') or ''
    rel_disp = es(rel)
    rq = lambda p: '/keep-files/' + quote(p)
    imgs = ''
    if is_image and rel:
        imgs = '<div class="img single"><img src="' + rq(rel) + '" alt=""></div>'
    elif isinstance(entry.get('images'), list) and entry['images']:
        imgs = '<div class="imgs">' + ''.join('<div class="img"><img src="' + rq(i.get('relPath', '')) + '" alt=""></div>' for i in entry['images']) + '</div>'
    html = '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>' + es(title) + ' · KeepAI</title>\n<style>\n:root{--bg:#f7f9fc;--bg2:#ffffff;--bg3:#edf1f6;--border:#d3dbe6;--text:#1c2733;--muted:#5b6b7c;--accent:#0a66c2}\n*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}\n.wrap{max-width:760px;margin:0 auto;padding:28px 20px 60px}\n.bar{display:flex;align-items:center;gap:10px;margin-bottom:20px}\n.bar a{color:var(--accent);text-decoration:none;font-size:13px}\n.note{background:var(--bg2);border:1px solid var(--border);border-radius:14px;overflow:hidden;box-shadow:0 10px 30px rgba(28,40,54,.1)}\n.imgs{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:3px;background:var(--bg3)}\n.imgs .img img{width:100%;height:200px;object-fit:cover;display:block}\n.img.single img{width:100%;max-height:60vh;object-fit:contain;display:block;background:var(--bg3)}\n.note-h{padding:18px 22px 6px}\n.note-h h1{margin:0;font-size:20px;line-height:1.4;word-break:break-word}\n.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}\n.tag{font-size:11px;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:2px 9px}\n.content{padding:6px 22px 22px;white-space:pre-wrap;word-break:break-word}\n.foot{margin-top:14px;color:var(--muted);font-size:12px}\n</style></head>\n<body><div class="wrap">\n<div class="bar"><a href="/">&#8592; Back to Live Transcription</a></div>\n<div class="note">' + imgs + '\n<div class="note-h"><h1>' + es(title) + '</h1>' + ('<div class="tags">' + ''.join('<span class="tag">' + es(l) + '</span>' for l in labels) + '</div>' if labels else '') + '\n</div>\n<div class="content">' + es(body) + '</div>\n</div>\n<div class="foot">Local Google Keep export &#183; ' + ('image' if is_image else 'note') + ' &#183; id: ' + es(nid) + ' &#183; ' + rel_disp + '</div>\n</div></body></html>' ' &#183; id: ' + es(nid) + ' &#183; ' + rel_disp + '</div>\n</div></body></html>'
    return html

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
    global model, topic_index, keep_index, keep_catalogue
    
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
    
    logger.info("Loading keep-ai knowledge base...")
    load_keep_data()
    
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