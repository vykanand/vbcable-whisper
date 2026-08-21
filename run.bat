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
echo  2. Web Server              (Live streaming at http://localhost:9000)
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
echo Starting web server - any old server will be stopped first...
echo.

:: Kill any existing process on port 9000
echo Checking for existing server...
taskkill /F /IM python.exe 2>nul
timeout /t 1 /nobreak >nul

:: Wait a moment for port to free
echo Waiting for port to become free...
timeout /t 2 /nobreak >nul

echo Starting web server...
python server.py

goto :end

:end
echo.
echo Application terminated.
pause