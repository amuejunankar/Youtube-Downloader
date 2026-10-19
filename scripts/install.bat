@echo off
REM ============================================================
REM  YouTube Downloader - dependency installer
REM  Creates a virtual environment (.venv) and installs
REM  requirements. Run from anywhere; paths are resolved
REM  relative to this script.
REM ============================================================
setlocal
cd /d "%~dp0.."

echo [1/4] Locating Python...
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% --version || (echo Python was not found. Install Python 3.11+ from https://www.python.org/downloads/ && pause && exit /b 1)

echo [2/4] Creating virtual environment in .venv ...
if not exist ".venv" (
    %PY% -m venv .venv || (echo Failed to create virtual environment. && pause && exit /b 1)
)

echo [3/4] Upgrading pip ...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip

echo [4/4] Installing requirements ...
python -m pip install -r requirements.txt || (echo Dependency installation failed. && pause && exit /b 1)

echo.
echo Checking FFmpeg (optional but recommended for high quality / MP3)...
where ffmpeg >nul 2>nul && (echo FFmpeg found on PATH.) || (echo FFmpeg NOT found. Install with:  winget install Gyan.FFmpeg)

echo.
echo ============================================================
echo  Installation complete.
echo  Launch the app with:  scripts\run.bat
echo ============================================================
pause
endlocal
