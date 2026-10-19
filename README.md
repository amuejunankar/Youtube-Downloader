# YouTube Downloader

A polished Windows desktop application (PySide6 + yt-dlp) for downloading
individual YouTube videos, complete playlists, or a hand-picked subset of
playlist entries at a quality you choose.

> Download only content you own or are otherwise authorised to download.
> This tool does not bypass DRM, paywalls, or authentication.

---

## Features

- **Video / playlist / selected-videos** download modes, chosen explicitly.
- **Dynamic quality selector** populated from the formats the video actually
  exposes (up to 4320p/8K, plus audio-only and MP3).
- **Accurate fallback reporting** — if you pick 4K and the video tops out at
  1080p, the app tells you the real resolution instead of silently lying.
- **Per-video status tracking**: completed / skipped / failed / cancelled.
- **Background threading** — the UI never freezes; cancel at any time.
- **Skip existing files**, resumable partial downloads, retries.
- **FFmpeg detection** with a clear warning and an install hint when missing.
- **Persistent logs** in `logs/app.log` (no cookies or secrets are logged).
- **Never downloads the whole playlist when you asked for one video.**

---

## 1. Install Python

Install **Python 3.11 – 3.13** from <https://www.python.org/downloads/>.
On the installer's first screen, tick **“Add python.exe to PATH”**.

Verify:

```bat
python --version
```

## 2. Create a virtual environment & install requirements

The easiest path is the helper script:

```bat
scripts\install.bat
```

This creates `.venv\`, upgrades pip, and installs `requirements.txt`.

Manual equivalent:

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> yt-dlp is updated frequently to keep up with YouTube changes. If extraction
> ever breaks, run `pip install -U yt-dlp` first.

## 3. Install / configure FFmpeg

FFmpeg is required for **high-resolution downloads** (YouTube serves video and
audio as separate streams above 360p), for **MP3 conversion**, and for
**post-download verification**. Without it the app falls back to single-file
formats only and says so.

Install with winget:

```bat
winget install Gyan.FFmpeg
```

…or with Chocolatey (`choco install ffmpeg`), or download a build from
<https://www.gyan.dev/ffmpeg/builds/> and add its `bin` folder to `PATH`.

If FFmpeg is installed outside `PATH`, set it in the app via
**File ▸ Set FFmpeg location…**.

FFprobe should come with FFmpeg. The app detects both and shows the status in
the banner at the top of the window. It also checks the common WinGet install
locations automatically, so a fresh `winget install Gyan.FFmpeg` is usually
picked up without restarting the shell.

### Optional but recommended: a JavaScript runtime

Recent versions of yt-dlp use a JavaScript runtime to solve YouTube's player
challenges. Without one, some videos expose fewer formats (or none). The app
also has an automatic fallback to the Android player client for videos the
default clients refuse, but installing a runtime improves format availability:

```bat
winget install DenoLand.Deno
```

(Installing Node.js works too.)


## 4. Run the GUI

```bat
scripts\run.bat
```

or:

```bat
.venv\Scripts\activate.bat
python main.py
```

Command-line options: `python main.py --version`, `python main.py --debug`,
`python main.py --selftest` (headless import check).

## 5. Using the app

### Analyze

1. Paste a URL (a video, a playlist, or a `watch?v=…&list=…` URL).
2. Click **Analyze URL**. Metadata loads in the background.

For a `watch?v=…&list=…` URL the app shows **both** interpretations and asks
you to pick a mode — it never guesses.

### Download one video

Choose **Single video only**, pick a quality, click **Start Download**.

### Download an entire playlist

1. Analyze the playlist URL.
2. Choose **Entire playlist**.
3. Pick a **maximum resolution** (each video downloads at the best format at or
   below it).
4. Optionally tick **Create a separate folder for each playlist**.
5. Click **Start Download**.

### Download selected playlist entries

1. Analyze the playlist.
2. Tick the videos you want (or use **Select All** / **Deselect All**).
3. Click **Download Selected** (this switches to *Selected playlist videos*).
4. Click **Start Download**.

Unavailable entries (private/deleted) are marked **No** in the *Available*
column, cannot be ticked, and are never counted as successful downloads.

### Selecting quality

The quality dropdown lists only resolutions the video actually provides, plus
**Best available quality**, **Audio only**, and **Audio only (MP3)** (MP3 needs
FFmpeg). The note under the dropdown shows the exact format that will be used,
including a clear message when a fallback occurs.

### Files & naming

- Default output: `%USERPROFILE%\Downloads\YouTube`.
- Playlist items are numbered `001 - Title.ext`.
- Illegitimate Windows filenames are sanitised automatically.
- Existing files are skipped when **Skip files that already exist** is ticked.

### Pause / cancel

yt-dlp does not support true pause/resume of an active transfer. **Cancel**
stops safely at a fragment boundary; partial `.part` data is kept and is
resumed if you start the same download again. There is no fake pause button.

---

## 6. Automated tests

```bat
scripts\run_tests.bat
```

or:

```bat
.venv\Scripts\activate.bat
pytest tests -v
```

The GUI smoke test runs headlessly (offscreen Qt platform) and is included in
the default run.

## 7. Real integration test (network)

Off by default so it never downloads anything unexpectedly:

```bat
set RUN_INTEGRATION=1
pytest tests/test_integration.py -v -s
```

This extracts metadata from the supplied playlist and inspects formats — no
download.

To also download **the first accessible video only**:

```bat
set RUN_INTEGRATION=1
set RUN_INTEGRATION_DOWNLOAD=1
pytest tests/test_integration.py::test_integration_download_first_video -v -s
```

To additionally exercise the FFmpeg **merge** path and **MP3 conversion**
(uses the CC-licensed Blender open movie and a 19-second public clip, not the
supplied playlist):

```bat
set RUN_INTEGRATION=1
set RUN_INTEGRATION_MERGE=1
pytest tests/test_integration.py -v -s
```

Optional: `set INTEGRATION_PLAYLIST_LIMIT=10` to limit how many entries are
listed. The app never downloads the whole playlist during testing.

### Known limitation observed in testing

The supplied playlist only exposes **360p combined** streams to the YouTube
clients reachable from the test machine (the default web client returns
`UNPLAYABLE`, and the Android fallback caps at 360p). High-resolution merging
therefore cannot be exercised on *that* playlist; it is verified separately
with the merge test above. This is a YouTube/content limitation, not a bug in
the app.


---

## 8. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ERROR: ... Unable to extract ...` / signature errors | YouTube changed something. Run `pip install -U yt-dlp`. |
| `HTTP Error 429` | Rate limited. Wait a few minutes and retry. |
| High resolutions unavailable | FFmpeg not detected. Install it, or pick a single-file quality. |
| `Requested format is not available` | The chosen resolution does not exist; pick another (the app usually falls back automatically). |
| Private / members-only / age-restricted video | Requires authentication; this app intentionally does not bypass it. |
| “could not find ffmpeg” after install | Reopen the terminal so `PATH` refreshes, or use **File ▸ Set FFmpeg location…**. |
| App won’t start, no window | Check `logs/app.log`; run `python main.py --debug`. |
| Only 360p / “Requested format is not available” | Install a JS runtime (`winget install DenoLand.Deno`). The app already retries with the Android client automatically. |
| “No supported JavaScript runtime” in the log | Harmless; install Deno/Node to unlock more formats. |
| Antivirus flags the built EXE | Expected for one-file PyInstaller builds; add an exclusion or use the folder build. |

Logs are written to `logs/app.log` (rotated). No cookies, tokens, or personal
data are ever written to the log.

---

## 9. Build a distributable Windows app (exe or installer)

The app can be shipped two ways. **Both are fully self-contained** — FFmpeg is
included, so nothing else has to be installed on the target machine.

### A. Single portable `.exe` (no installation)

```bat
scripts\build_exe.bat
```

Produces `dist\YouTubeDownloader.exe` (~225 MB) — one file, double-click to run,
FFmpeg embedded. Ideal for USB sticks or quick sharing. First start takes a few
seconds while the file unpacks.

### B. Windows installer (`Setup.exe`)

```bat
set YD_BUNDLE_FFMPEG=1
scripts\build_exe.bat folder
installer\build_installer.bat
```

Produces `dist\YouTubeDownloader-Setup-1.0.0.exe` (~157 MB). Running it installs
the app (per-user by default, no admin required), adds Start-Menu and optional
desktop shortcuts, and registers an uninstaller. Installed apps start instantly
because they are not unpacked on every launch.

`installer\build_installer.bat` needs **Inno Setup 6**
(`winget install JRSoftware.InnoSetup`).

### Other build options

| Command | Result |
| --- | --- |
| `scripts\build_exe.bat onefile` | Single `.exe`, FFmpeg embedded (default). |
| `scripts\build_exe.bat folder` | Folder build, FFmpeg embedded. |
| `scripts\build_exe.bat both` | Both of the above. |
| `set YD_BUNDLE_FFMPEG=0` first | Smaller build (~57 MB exe); FFmpeg detected from `PATH`/WinGet at runtime or added via **File ▸ Set FFmpeg location…**. |

Builds are produced in `dist\`. FFmpeg/FFprobe are taken from wherever the app
detects them (`scripts\find_ffmpeg.py`), and the icon is generated by
`scripts\make_icon.py`.

> **Honesty note:** the executables and installer above were built and
> smoke-tested here (`--selftest`, a real GUI launch, plus a silent
> install → run → uninstall cycle). Always launch the produced `.exe` once
> yourself after building. Antivirus tools sometimes flag freshly built one-file
> PyInstaller executables — add an exclusion if needed.

---

## 10. Project structure

```
youtube_downloader/
  main.py                 # entry point + logging
  YouTubeDownloader.spec  # PyInstaller build definition
  requirements.txt
  README.md
  .gitignore
  app/
    main_window.py        # PySide6 GUI
    downloader.py         # yt-dlp engine (Qt-free, testable)
    format_service.py     # format discovery / quality selection
    workers.py            # QThread workers + Qt signals
    settings.py           # JSON settings store
    utils.py              # URL/filename/FFmpeg helpers
  tests/
    test_utils.py
    test_format_selection.py
    test_downloader.py
    test_integration.py   # network + GUI smoke tests
  scripts/
    install.bat  run.bat  run_tests.bat  build_exe.bat
    make_icon.py          # generates assets/app.ico
    find_ffmpeg.py        # locates FFmpeg for the build
  installer/
    YouTubeDownloader.iss # Inno Setup script
    build_installer.bat   # builds dist\YouTubeDownloader-Setup-*.exe
  assets/       # generated app icon (git-ignored)
  logs/         # runtime logs (git-ignored)
  downloads/    # default scratch output (git-ignored)
```

---

## 11. Security & privacy

- No administrator privileges required.
- No data is collected or uploaded.
- No credentials, cookies, or browser profiles are used by default.
- TLS verification is never disabled.
- URLs are validated and never passed to a shell.
- Secrets are never written to logs.
