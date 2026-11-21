@echo off
REM ============================================================
REM  Build the Windows installer (Setup.exe) with Inno Setup.
REM
REM  The script builds the app bundle first when it is missing
REM  (folder build with FFmpeg embedded) and takes the version
REM  from app\__init__.py, so the installer always matches the
REM  app it ships.
REM
REM  Usage:
REM    installer\build_installer.bat            installer only
REM    installer\build_installer.bat portable   also build the one-file .exe
REM
REM  Prerequisites:
REM     scripts\install.bat                  once, creates .venv
REM     winget install JRSoftware.InnoSetup  Inno Setup 6
REM
REM  Output: dist\YouTubeDownloader-Setup-<version>.exe
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

set "MODE=%~1"

REM ---- 1/5  Python (virtual environment preferred) -----------------------
if exist ".venv\Scripts\python.exe" (
    set "PY=%CD%\.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

REM ---- 2/5  App folder build ---------------------------------------------
if not exist "dist\YouTubeDownloader\YouTubeDownloader.exe" (
    echo [1/5] App bundle not found - building dist\YouTubeDownloader ...
    set "YD_NOPAUSE=1"
    if not defined YD_BUNDLE_FFMPEG set "YD_BUNDLE_FFMPEG=1"
    call scripts\build_exe.bat folder
    if not exist "dist\YouTubeDownloader\YouTubeDownloader.exe" (
        echo.
        echo Could not build dist\YouTubeDownloader\YouTubeDownloader.exe
        pause
        exit /b 1
    )
) else (
    echo [1/5] Using existing bundle in dist\YouTubeDownloader
)

REM ---- 3/5  Version (single source of truth: app\__init__.py) ------------
set "APPVER="
"%PY%" -c "import app; print(app.__version__)" > "%TEMP%\yd_version.txt" 2>nul
if exist "%TEMP%\yd_version.txt" set /p APPVER=<"%TEMP%\yd_version.txt"
del "%TEMP%\yd_version.txt" >nul 2>nul
if not defined APPVER (
    echo Could not read the app version from app\__init__.py
    pause
    exit /b 1
)
echo [2/5] App version: %APPVER%

REM ---- 4/5  FFmpeg inside the bundle -------------------------------------
if exist "dist\YouTubeDownloader\_internal\ffmpeg.exe" goto :ffmpeg_ok
if exist "dist\YouTubeDownloader\ffmpeg.exe" goto :ffmpeg_ok
echo [3/5] WARNING: no ffmpeg.exe in the bundle - MP3 and high-resolution
echo        merging will need FFmpeg installed on the target machine.
echo        Rebuild with:  set YD_BUNDLE_FFMPEG=1 ^&^& scripts\build_exe.bat folder
goto :find_iscc
:ffmpeg_ok
echo [3/5] FFmpeg is bundled - the installer is fully self-contained.

REM ---- 5/5  Inno Setup compiler ------------------------------------------
:find_iscc
set "ISCC="
for %%P in (
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if not defined ISCC if exist %%P set "ISCC=%%~P"
if not defined ISCC for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%~P"

if not defined ISCC (
    echo [4/5] Inno Setup 6 was not found. Install it with:
    echo           winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

echo [4/5] Compiling installer with "%ISCC%" ...
"%ISCC%" /DAppVersion=%APPVER% "installer\YouTubeDownloader.iss"
if errorlevel 1 (
    echo Installer build failed.
    pause
    exit /b 1
)

if /I "%MODE%"=="portable" (
    echo [5/5] Building the one-file portable .exe ...
    set "YD_ONEFILE=1"
    set "YD_BUNDLE_FFMPEG=1"
    set "YD_NOPAUSE=1"
    call scripts\build_exe.bat onefile
    set "YD_ONEFILE="
)

echo.
echo ============================================================
echo  Installer built: dist\YouTubeDownloader-Setup-%APPVER%.exe
if /I "%MODE%"=="portable" echo  Portable      : dist\YouTubeDownloader.exe
echo ============================================================
pause
endlocal
