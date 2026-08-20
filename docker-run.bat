@echo off
title Docker Audio Call Transcriber

echo.
echo ============================================================
echo      Docker Audio Call Transcriber Runner
echo ============================================================
echo.

cd /d %~dp0

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker not found in PATH
    echo Please install Docker Desktop for Windows
    pause
    exit /b 1
)

echo Building Docker image...
docker build -t call-transcriber .

echo.
echo WARNING: Audio access from Docker on Windows requires:
echo  1. Docker Desktop with WSL 2 backend
echo  2. Volume mount: -v /dev/bus/usb:/dev/bus/usb (Linux path)
echo  3. On Windows, PulseAudio or similar is needed for audio
echo.
echo For best results on Windows, use run.bat instead of Docker.
echo.

pause

echo.
echo Running container...
echo Press Ctrl+C to stop
echo.

docker run --rm -it -v "%cd%\transcripts:/app/transcripts" call-transcriber

echo.
echo Container stopped. Transcripts saved to transcripts/ folder.