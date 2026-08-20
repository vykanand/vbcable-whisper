# Two-Way Speech-to-Text Call Transcriber

A real-time, ultra-fast, two-way speech-to-text transcription tool for computer audio calls (Zoom, Teams, Discord, etc.) using local `faster-whisper` models.

## Features

- **Dual-channel interception**: Captures microphone input and system audio simultaneously via multi-threading
- **Local transcription**: Uses `faster-whisper` (base model) with CPU-only, no internet required
- **Real-time VAD**: Voice Activity Detection with rolling buffer for near-zero latency
- **Formatted output**: Timestamped, source-identified transcriptions in terminal and markdown

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
[0] Microsoft Sound Mapper - Input | Input channels: 2 | Output channels: 0
[1] Microphone Array (Intel) | Input channels: 4 | Output channels: 0
[2] Speakers (Realtek) | Input channels: 0 | Output channels: 8

Enter the index ID for your Microphone: 1
Enter the index ID for your Speaker/Loopback device: 0
```

### Step 3: Monitor Output

Live transcriptions appear in the terminal:
```
[14:30:25] [MIC]: "Hello, how are you doing today?"
[14:30:28] [SYS]: "I'm doing well, thanks for asking."
```

### Step 4: Stop & Save

Press `Ctrl+C` to stop. All transcriptions are saved to `call_transcript_YYYYMMDD_HHMMSS.md` in the current directory.

## Output Format

```
[HH:MM:SS] [MIC]: "Your spoken words here..."
[HH:MM:SS] [SYS]: "Incoming call audio words here..."
```

## Transcript File

All transcriptions are appended to `call_transcript_[TIMESTAMP].md`:

```markdown
# Call Transcript – 2024-01-15T14:30:22.123456

[14:30:25] [MIC]: "Hello, how are you doing today?"
[14:30:28] [SYS]: "I'm doing well, thanks for asking."
```

## Architecture

- **Audio Capture**: Two daemon threads read from PyAudio streams (MIC + SYS)
- **Queue**: Thread-safe queues pass audio chunks to transcription workers
- **VAD**: Simple RMS energy-based Voice Activity Detection
- **Transcription**: `faster-whisper/base` model runs locally on CPU with int8 quantization

## Configuration

The following constants in `main.py` can be adjusted:

- `ENERGY_THRESHOLD`: VAD sensitivity (higher = less sensitive)
- `CHUNK_SIZE`: Audio frames per buffer (default 1024)
- `RATE`: Audio sample rate (default 16000 Hz)
- Model size: Change `"base"` to `"small"` or `"tiny"` in `WhisperModel()` call

## Termination

Press `Ctrl+C` to stop transcription. The application will:
- Safely close all audio streams
- Flush pending transcriptions
- Save the markdown file

## Troubleshooting

**"Could not open audio streams"**: Verify device indices in the device list match available hardware.

**No input channels for expected device**: On Windows, ensure "Stereo Mix" or equivalent is enabled in Sound settings.

**Silent transcriptions**: Adjust `ENERGY_THRESHOLD` in the code or check microphone/system audio levels.

**High CPU usage**: Use a smaller model (`tiny`) or larger chunk size for less frequent transcription calls.