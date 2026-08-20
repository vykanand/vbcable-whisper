@echo off
title Web Transcription Server

echo.
echo ============================================================
echo      Live Transcription Web Server
echo ============================================================
echo.

cd /d %~dp0

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH
    echo Please install Python 3.10+ and add it to PATH
    pause
    exit /b 1
)

echo Checking dependencies...
python -c "import pyaudio" 2>nul
if errorlevel 1 (
    echo Installing pyaudio...
    pip install pyaudio --quiet
)

python -c "import faster_whisper" 2>nul
if errorlevel 1 (
    echo Installing faster-whisper and dependencies...
    pip install faster-whisper numpy --quiet
)

echo.
echo Starting web server...
echo Open http://localhost:8080 in your browser
echo.
python server.py

echo.
echo Server stopped.