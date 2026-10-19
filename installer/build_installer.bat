@echo off
REM ============================================================
REM  Build the Windows installer (Setup.exe) with Inno Setup.
REM
REM  Prerequisites:
REM    1. Build the bundled folder app first:
REM         set YD_BUNDLE_FFMPEG=1
REM         scripts\build_exe.bat folder
REM    2. Inno Setup 6 installed (winget install JRSoftware.InnoSetup)
REM
REM  Output: dist\YouTubeDownloader-Setup-<version>.exe
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

if not exist "dist\YouTubeDownloader\YouTubeDownloader.exe" (
    echo Folder build not found: dist\YouTubeDownloader\YouTubeDownloader.exe
    echo Run:  set YD_BUNDLE_FFMPEG=1 ^&^& scripts\build_exe.bat folder
    pause
    exit /b 1
)

set "ISCC="
for %%P in (
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if not defined ISCC if exist %%P set "ISCC=%%~P"

if not defined ISCC (
    echo Inno Setup 6 was not found. Install it with:
    echo     winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

echo Using: "%ISCC%"
"%ISCC%" "installer\YouTubeDownloader.iss" || (
    echo Installer build failed. & pause & exit /b 1
)

echo.
echo ============================================================
echo  Installer built: dist\YouTubeDownloader-Setup-*.exe
echo ============================================================
pause
endlocal
