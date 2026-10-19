@echo off
REM ============================================================
REM  YouTube Downloader - run the automated test suite
REM  Usage:
REM    scripts\run_tests.bat                 (unit tests only)
REM    scripts\run_tests.bat integration     (adds real network tests)
REM ============================================================
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)

if /I "%~1"=="integration" (
    set "RUN_INTEGRATION=1"
    echo Running unit + integration tests (network access required)...
) else (
    echo Running unit tests...
)

%PY% -m pytest tests -v
endlocal
