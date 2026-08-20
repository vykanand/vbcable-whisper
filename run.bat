@echo off
title Audio Call Transcriber - Menu

echo.
echo ============================================================
echo      Two-Way Speech-to-Text Call Transcriber
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
echo ============================================================
echo        What would you like to run?
echo ============================================================
echo.
echo  1. Live Transcription Test  (Terminal output, saves to markdown)
echo  2. Web Server              (Live streaming at http://localhost:8080)
echo  3. Exit
echo.

choice /c 123 /m "Select option"
if errorlevel 3 goto :eof
if errorlevel 2 goto :webserver
if errorlevel 1 goto :test

:test
echo.
echo Starting transcription test...
echo.
python main.py
goto :end

:webserver
echo.
echo Starting web server...
echo Open http://localhost:8080 in your browser
echo.
python server.py

:end
echo.
echo Application terminated.
pause