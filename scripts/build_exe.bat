@echo off
REM ============================================================
REM  YouTube Downloader - build a standalone Windows desktop app
REM
REM  Default: a single self-contained .exe with FFmpeg embedded,
REM  so nothing else needs to be installed on the target machine.
REM
REM  Usage:
REM    scripts\build_exe.bat             single-file exe, FFmpeg bundled
REM    scripts\build_exe.bat folder      folder build (faster start)
REM    scripts\build_exe.bat onefile     single-file build
REM    scripts\build_exe.bat both        build both
REM
REM  Environment:
REM    YD_BUNDLE_FFMPEG=0    do NOT embed FFmpeg (smaller, needs FFmpeg
REM                          installed / on PATH at runtime)
REM    YD_BUNDLE_FFMPEG=1    embed FFmpeg (default)
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

set "MODE=%~1"
if "%MODE%"=="" set "MODE=onefile"
if not defined YD_BUNDLE_FFMPEG set "YD_BUNDLE_FFMPEG=1"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run scripts\install.bat first.
    pause
    exit /b 1
)
set "PY=%CD%\.venv\Scripts\python.exe"

echo [1/4] Ensuring PyInstaller is installed...
"%PY%" -m pip install --upgrade pyinstaller || (
    echo Could not install PyInstaller. & pause & exit /b 1
)

echo [2/4] Generating application icon...
"%PY%" scripts\make_icon.py || (
    echo Icon generation failed. & pause & exit /b 1
)

if "%YD_BUNDLE_FFMPEG%"=="1" (
    echo [3/4] FFmpeg will be EMBEDDED ^(fully self-contained^).
) else (
    echo [3/4] FFmpeg will NOT be embedded ^(detected at runtime^).
)

if /I not "%MODE%"=="onefile" (
    echo [4/4] Building FOLDER build ^(dist\YouTubeDownloader\^)...
    set "YD_ONEFILE="
    "%PY%" -m PyInstaller --noconfirm --clean YouTubeDownloader.spec || (
        echo Build failed. & pause & exit /b 1
    )
)

if /I not "%MODE%"=="folder" (
    echo [4/4] Building SINGLE-FILE build ^(dist\YouTubeDownloader.exe^)...
    set "YD_ONEFILE=1"
    "%PY%" -m PyInstaller --noconfirm --clean YouTubeDownloader.spec || (
        echo Build failed. & pause & exit /b 1
    )
    set "YD_ONEFILE="
)

echo.
echo ============================================================
echo  Build complete.
if /I not "%MODE%"=="onefile" echo   Folder : dist\YouTubeDownloader\YouTubeDownloader.exe
if /I not "%MODE%"=="folder"   echo   Single : dist\YouTubeDownloader.exe
echo.
echo  To build a Setup.exe installer:
echo     installer\build_installer.bat
echo ============================================================
pause
endlocal
