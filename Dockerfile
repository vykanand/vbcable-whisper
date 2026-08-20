FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pyaudio>=0.2.14 \
    faster-whisper>=1.2.1 \
    numpy>=1.24.0 \
    ctranslate2>=4.0

WORKDIR /app

COPY main.py .
COPY requirements.txt .

VOLUME ["/app/transcripts"]
WORKDIR /app

RUN mkdir -p /app/transcripts

CMD ["python", "main.py"]