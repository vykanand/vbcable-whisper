# Two-Way Speech-to-Text Call Transcriber

A real-time, ultra-fast, two-way speech-to-text transcription tool for computer audio calls (Zoom, Teams, Discord, etc.) using local `faster-whisper` models.

## Features

- **Dual-channel interception**: Captures microphone input and system audio simultaneously via multi-threading
- **Local transcription**: Uses `faster-whisper` (small model) with CPU-only, no internet required
- **Real-time VAD**: Voice Activity Detection with rolling buffer for near-zero latency
- **Formatted output**: Timestamped, source-identified transcriptions in terminal and markdown
- **Web dashboard**: Live transcription streaming with SSE/WebSockets
- **Topic matching**: Automatic matching of SYS-side speech to topics.json with clickable links

## Requirements

- Python 3.10+
- Working audio input device (microphone)
- Working audio output device with loopback capture capability

### Platform-Specific Requirements

**Windows**: 
- **Option 1**: Enable "Stereo Mix" or "What U Hear" in Windows Sound settings (Devices → Recording → Show disabled devices → Enable Stereo Mix)
- **Option 2 (recommended)**: Install **VB-Audio Virtual Cable** (vb-audio.com). It creates:
  - **CABLE Input** (a playback device) — set this as your **default playback** so call/system audio is routed into the cable
  - **CABLE Output** (a recording device) — this is what the app captures as **SYS**
- **Option 3**: Use the actual output device if it exposes a WASAPI loopback input (e.g., "Speakers (Realtek) - Input" or similar)

**macOS**:
- Install BlackHole, Soundflower, or use Aggregate Device
- Enable input monitoring on the virtual device

**Linux**:
- Use PulseAudio monitor modules (`pactl load-module module-loopback`)
- Or create a virtual sink/input monitor

## Installation

```bash
pip install -r requirements.txt
```

On Windows, if `pyaudio` fails:
```powershell
# Using Chocolatey (admin PowerShell)
choco install portaudio

# Or download pre-built wheel from:
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
pip install PyAudio‑0.2.14‑cp311‑cp311‑win_amd64.whl
```

## Quick Start (Windows)

On Windows, simply double-click `run.bat` to:
1. Check and install Python dependencies if needed
2. Start the transcription application directly

```powershell
.\run.bat
```

## VB-Audio Cable Setup (Windows, recommended for SYS capture)

1. Install **VB-Audio Virtual Cable** from https://vb-audio.com/Cable/
2. Run the provided setup script (installs the `AudioDeviceCmdlets` PowerShell module on first run, then sets defaults):
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup_vb_cable.ps1
   ```
   This sets **CABLE Input** as the default *playback* device and your USB headset mic as default *recording*.
3. Verify the loopback works (plays a tone into CABLE Input, measures CABLE Output):
   ```powershell
   python test_loopback.py
   ```
   Expect `CABLE Output captured maxRMS = <large number>` and `[OK] Loopback works`.
4. **To still hear the call**, enable *Listen to this device* on **CABLE Input**
   (Sound → Recording → CABLE Input → Properties → Listen → "Play through this device" → your USB headset).
5. The app auto-detects devices by name, so just run `python main.py` — it picks
   your USB ENC mic as **MIC** and **CABLE Output** as **SYS** automatically.

> Note: with CABLE Input as the default playback device, all system audio is
> silently routed into the cable. That is expected — the app captures it as SYS,
> and "Listen to this device" lets you hear it too.

## Web Dashboard

For a live web interface with topic matching, run:

```powershell
.\server.bat            # Windows batch
# or
python server.py        # Direct Python
```

Then open http://localhost:8080 in your browser.

### Features

- **Live transcriptions**: See MIC and SYS speech as it happens
- **Color-coded**: MIC in blue, SYS in green, matched topics in orange
- **Topic matching**: When SYS speech matches topics in `topics.json`, clickable links appear

## Topic Matching

The system matches spoken words from SYS (system audio) against `topics.json` to provide:
- Real-time topic identification
- Clickable links to resources (Google Keep, Google Docs, Medium articles, etc.)

### How it works

1. `topics.json` contains structured topics with keywords and resource URLs
2. When SYS speech is transcribed, keywords are matched against the topic index
3. Matched topics are displayed in the web dashboard with links

### Example

If someone says: *"How would you design Architecture for microservices?"*

The system matches "Architecture", "microservices", "design" and displays:
- **"How would you design Architecture?" →** https://docs.google.com/document/...
- **"Microservice Patterns and Application Design" →** https://keep.google.com/...

## Docker (Linux/macOS)

```bash
docker build -t call-transcriber .
docker run --rm -it -v "$(pwd)/transcripts:/app/transcripts" call-transcriber
```

## Usage

### Step 1: Run the Application

**Windows Batch Script (Recommended)**
```powershell
.\run.bat
```

**Web Server**
```bash
python server.py
```

**Direct Python**
```bash
python main.py
```

**Docker**
```bash
docker build -t call-transcriber .
docker run --rm -it -v "$(pwd)/transcripts:/app/transcripts" call-transcriber
```

### Step 2: Select Audio Devices

When prompted, enter the device indices shown in the device list:
```
[0] Microsoft Sound Mapper - Input | Input: 2 | Output: 0
[1] Microphone (USB ENC Audio Devic | Input: 4 | Output: 0 | Host: 0
[2] CABLE Output (VB-Audio Virtual  | Input: 16 | Output: 0 | Host: 0

Enter the index ID for your Microphone: 1
Enter the index ID for your Speaker/Loopback device: 2
```

### Step 3: Monitor Output

Live transcriptions appear in the terminal:
```
[14:30:25] [MIC]: "Hello, how are you doing today?"
[14:30:28] [SYS]: "I'm doing well, thanks for asking."
```

Or view them in the web dashboard at http://localhost:8080

### Step 4: Stop & Save

Press `Ctrl+C` to stop. All transcriptions are saved to `call_transcript_YYYYMMDD_HHMMSS.md` in the current directory.

## Output Format

```
[HH:MM:SS] [MIC]: "Your spoken words here..."
[HH:MM:SS] [SYS]: "Incoming call audio words here..."
[HH:MM:SS] [MATCH] Resource Title -> https://...
```

## Transcript File

All transcriptions are appended to `call_transcript_[TIMESTAMP].md`:

```markdown
# Call Transcript – 2024-01-15T14:30:22.123456

[14:30:25] [MIC]: "Hello, how are you doing today?"
[14:30:28] [SYS]: "I'm doing well, thanks for asking."
```

## Files

- `main.py` — Core transcription library
- `server.py` — Web server with live streaming
- `run.bat` — Windows batch launcher
- `server.bat` — Windows batch for web server
- `Dockerfile` — Docker build for Linux/macOS
- `topics.json` — Topic index for matching
- `requirements.txt` — Python dependencies
- `setup_vb_cable.ps1` — VB-Audio setup script
- `test_loopback.py` — Loopback verification script

## Architecture

- **Audio Capture**: Two daemon threads read from PyAudio streams (MIC + SYS)
- **Queue**: Thread-safe queues pass audio chunks to transcription workers
- **VAD**: Simple RMS energy-based Voice Activity Detection
- **Transcription**: `faster-whisper/small` model runs locally on CPU with int8 quantization
- **WS Server**: HTTP server with SSE endpoint for live streaming to web

## Configuration

The following constants can be adjusted:

- `ENERGY_THRESHOLD`: VAD sensitivity (higher = less sensitive, default 50)
- `CHUNK_SIZE`: Audio frames per buffer (default 1024)
- `RATE`: Audio sample rate (default 16000 Hz)
- Model size: Change `"small"` to `"tiny"`, `"base"`, or `"medium"` in code

## Troubleshooting

**"Could not open audio streams"**: Verify device indices in the device list match available hardware.

**No input channels for expected device**: On Windows, ensure "Stereo Mix" or equivalent is enabled in Sound settings.

**Silent transcriptions**: Adjust `ENERGY_THRESHOLD` in the code or check microphone/system audio levels.

**High CPU usage**: Use a smaller model (`tiny`) or increase silence threshold for longer segments before flushing.

**Topic matching not working**: Check `topics.json` structure and ensure SYS is capturing system audio (CABLE Output).