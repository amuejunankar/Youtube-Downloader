@echo off
REM ============================================================
REM  YouTube Downloader - launcher
REM  Uses the project virtual environment if present,
REM  otherwise falls back to the system Python.
REM ============================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)

echo Launching YouTube Downloader...
%PY% main.py %*

if errorlevel 1 (
    echo.
    echo The application exited with an error. See logs\app.log for details.
    pause
)
endlocal
