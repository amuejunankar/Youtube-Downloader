# Test Report — YouTube Downloader

Generated from an actual run on the build machine. Nothing below is simulated.

## Environment

| Item | Value |
| --- | --- |
| OS | Windows (win32) |
| Python | 3.13.1 |
| yt-dlp | 2026.8.19 |
| PySide6 | 6.12.0 |
| pytest | 9.1.1 |
| FFmpeg / FFprobe | **Not present at start**; installed during testing via `winget install Gyan.FFmpeg` → FFmpeg 9.0.2 (Gyan full build) |
| JS runtime | Deno 2.9.7 installed during testing (optional) |
| Network | Reachable (youtube.com:443, pypi.org:443) |

## Unit tests (network mocked)

Command:

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

Result: **101 passed, 6 skipped** (the 6 skipped are the opt-in network tests).

Coverage includes: URL validation/classification, filename sanitisation,
settings persistence, FFmpeg detection, format selection & fallbacks,
download result/status tracking, skip-existing, cancellation, progress hooks
with unknown sizes, error-message mapping, playlist parsing, and Qt worker
signal wiring.

## GUI launch test

- Headless construction + 1.5 s event loop: exit code **0**.
- Window title, 12 quality entries, green FFmpeg banner confirmed.
- Signal/slot interaction test (quality change, mode radios, URL change): passed.

## Integration test — supplied playlist

URL tested verbatim:
`https://www.youtube.com/watch?v=CUJcKAF5wG8&list=PLV-cxl3VSwWE_ocGSxzOhIrLp4X1cDcxk`

| Check | Result |
| --- | --- |
| Metadata extraction | **PASS** |
| Playlist title | `Bestie Quiet Playtime \| Calm Music for Toddlers` |
| Entries returned | **4** (all available) |
| Video-in-URL title | `Quiet Playtime with Bestie \| ChuChu TV Calm Music for Toddlers (60 Minutes)` |
| Formats for first video | **5** |
| Available video heights | **360p only** (see limitation) |
| First-video download | **PASS** — `completed` |
| Output file | `Quiet Playtime with Bestie ｜ ChuChu TV Calm Music for Toddlers (60 Minutes).mp4` |
| Size | **101.0 MiB** |
| Elapsed | **27.8 s** |
| FFprobe | **ok** — mp4, duration 3752.95 s, video **h264**, audio **aac** |

### Playlist entries observed

| # | Duration | Available | Title |
| --- | --- | --- | --- |
| 1 | 1:02:33 | Yes | Quiet Playtime with Bestie \| ChuChu TV Calm Music for Toddlers (60 Minutes) |
| 2 | 32:11 | Yes | Quiet Playtime with Bestie \| Calm Music for Toddlers by ChuChu TV |
| 3 | 2:17:30 | Yes | 2 Hours Super Relaxing Nursery Rhymes Lullabies (NO ADS) - ChuChu TV Bedtime Lullaby Sleep Music |
| 4 | 2:00:54 | Yes | AD FREE 2 Hours Super Relaxing Baby Music - 2 Hours of Baby Sleep Music (NO ADS) - Christmas Tunes |

## Additional real tests

| Test | Result |
| --- | --- |
| Error handling (nonexistent video id) | **PASS** — `failed` with a friendly, non-traceback message |
| FFmpeg merge (720p separate streams → mp4) | **PASS** — 77.8 MiB, FFprobe: **av1 720p + opus** |
| MP3 conversion (audio-only) | **PASS** — 447.7 KiB `.mp3`, FFprobe audio codec **mp3** |
| Skip-existing / cancellation / progress | **PASS** (unit, mocked yt-dlp) |

Merge and MP3 tests use the CC-licensed Blender open movie
(`aqz-KE-bpKQ`) and the 19-second public clip `jNQXAC9IVRw`, because the
supplied playlist does not expose separate high-resolution streams in this
environment. They are opt-in (`RUN_INTEGRATION_MERGE=1`) and never substitute
for the supplied-playlist test.

## Limitation

The supplied playlist only exposes a **360p combined** stream through the
YouTube clients reachable from this machine. The default web/visionOS clients
return `UNPLAYABLE`; the app automatically retries with the Android player
client (which works, but caps at 360p). Therefore high-resolution merging is
verified separately, not on this playlist. This is a YouTube/content
limitation, not an application bug.

## Commands

```bat
REM install + run
scripts\install.bat
scripts\run.bat

REM unit tests
.venv\Scripts\python.exe -m pytest tests -q

REM real playlist metadata + formats
set RUN_INTEGRATION=1
.venv\Scripts\python.exe -m pytest tests/test_integration.py -v -s

REM real first-video download
set RUN_INTEGRATION_DOWNLOAD=1
.venv\Scripts\python.exe -m pytest tests/test_integration.py::test_integration_download_first_video -v -s

REM merge + MP3
set RUN_INTEGRATION_MERGE=1
.venv\Scripts\python.exe -m pytest tests/test_integration.py -v -s
```
