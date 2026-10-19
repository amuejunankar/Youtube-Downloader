; Inno Setup script for the YouTube Downloader desktop app.
;
; Build the bundled folder app first, then compile this script:
;
;     scripts\build_exe.bat folder        REM with YD_BUNDLE_FFMPEG=1
;     installer\build_installer.bat
;
; The installer is per-user by default (no admin needed) and also offers an
; all-users install to Program Files. It ships the app together with FFmpeg,
; so nothing else has to be installed.

#define AppName "YouTube Downloader"
#define AppVersion "1.0.0"
#define AppPublisher "YouTube Downloader"
#define AppExeName "YouTubeDownloader.exe"
#define SourceDir "..\dist\YouTubeDownloader"

[Setup]
AppId={{8F3E1B2A-7C4D-4E9F-A6B1-2D5C9E7F0A34}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=YouTubeDownloader-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
