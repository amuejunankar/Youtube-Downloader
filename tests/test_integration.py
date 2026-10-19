"""Integration tests + GUI smoke test.

Network tests are **off by default**. Enable them explicitly:

    set RUN_INTEGRATION=1
    pytest tests/test_integration.py -v -s

To also perform a real download smoke test (first accessible video only):

    set RUN_INTEGRATION_DOWNLOAD=1
    pytest tests/test_integration.py -v -s

The supplied playlist is used verbatim and is never silently substituted.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Console output may contain non-ASCII titles; make printing robust on Windows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

from app import utils
from app.downloader import DownloadService
from app.format_service import resolve_format

TEST_URL = utils.DEFAULT_TEST_URL

RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION") == "1"
RUN_DOWNLOAD = os.environ.get("RUN_INTEGRATION_DOWNLOAD") == "1"
PLAYLIST_LIMIT = int(os.environ.get("INTEGRATION_PLAYLIST_LIMIT", "50"))

requires_net = pytest.mark.skipif(
    not RUN_INTEGRATION, reason="set RUN_INTEGRATION=1 to run network tests"
)


# --------------------------------------------------------------------------- #
# GUI smoke test (always runs; uses the offscreen Qt platform)
# --------------------------------------------------------------------------- #

def test_gui_imports_and_launches(tmp_path):
    """Import every GUI module and construct the main window headlessly."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"PySide6 not installed: {exc}")

    # Import all application modules to catch syntax/import errors.
    import app.main_window  # noqa: F401
    import app.workers  # noqa: F401
    from app.main_window import MainWindow
    from app.settings import Settings
    from app.utils import FFmpegStatus

    qt_app = QApplication.instance() or QApplication([])
    settings = Settings(tmp_path / "settings.json")
    window = MainWindow(settings=settings, ffmpeg=FFmpegStatus())
    assert window.windowTitle().startswith("YouTube Downloader")
    assert window.quality_combo.count() > 0
    assert window.btn_start.isEnabled()
    assert window.btn_cancel.isEnabled() is False
    # The URL field starts empty; nothing is downloaded automatically.
    assert window.url_input.text() == ""
    window.close()
    assert qt_app is not None


# --------------------------------------------------------------------------- #
# Real metadata extraction
# --------------------------------------------------------------------------- #

@requires_net
def test_integration_metadata_extraction(capsys):
    """Extract the supplied playlist's metadata (no download)."""
    svc = DownloadService()
    started = time.time()
    res = svc.analyze(TEST_URL, playlist_limit=PLAYLIST_LIMIT)
    elapsed = time.time() - started

    with capsys.disabled():
        print("\n===== INTEGRATION: METADATA =====")
        print(f"URL            : {TEST_URL}")
        print(f"kind           : {res.kind}")
        print(f"video title    : {res.title}")
        print(f"playlist title : {res.playlist_title}")
        print(f"entry count    : {res.entry_count}")
        print(f"video formats  : {len(res.formats)}")
        print(f"error          : {res.error}")
        print(f"elapsed        : {elapsed:.2f}s")
        for e in res.entries[:10]:
            print(f"  [{e.index}] {'OK ' if e.available else 'NO '} "
                  f"{utils.human_duration(e.duration):>8}  {e.title}")
        print("=================================\n")

    assert res.error is None, f"metadata extraction failed: {res.error}"
    assert res.entry_count > 0, "playlist returned zero entries"
    assert res.is_playlist
    # The URL contains a video id too, so a single-video extraction should work.
    assert res.has_video
    assert res.formats, "no formats returned for the video in the URL"


@requires_net
def test_integration_format_discovery(capsys):
    """Fetch formats for the first accessible playlist entry."""
    svc = DownloadService()
    res = svc.analyze(TEST_URL, playlist_limit=PLAYLIST_LIMIT)
    first = next((e for e in res.entries if e.available and e.url), None)
    if first is None:
        pytest.skip("no accessible playlist entries to inspect")

    formats = svc.get_formats(first.url)
    from app.format_service import available_video_heights
    heights = available_video_heights(formats)

    with capsys.disabled():
        print("\n===== INTEGRATION: FORMATS =====")
        print(f"video          : {first.title}")
        print(f"format count   : {len(formats)}")
        print(f"heights (desc) : {heights}")
        for key in ("best", str(heights[0]) if heights else "720"):
            r = resolve_format(key, formats, ffmpeg_available=svc.ffmpeg_available)
            print(f"  quality={key:<6} -> {r.describe()}")
        print("================================\n")

    assert formats, "no formats returned"


# --------------------------------------------------------------------------- #
# Real download smoke test (first accessible video only)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not RUN_DOWNLOAD,
                    reason="set RUN_INTEGRATION_DOWNLOAD=1 to run the download smoke test")
@requires_net
def test_integration_download_first_video(tmp_path, capsys):
    """Download the first accessible video and verify the resulting file."""
    svc = DownloadService()
    res = svc.analyze(TEST_URL, playlist_limit=PLAYLIST_LIMIT)
    first = next((e for e in res.entries if e.available and e.url), None)
    if first is None:
        pytest.skip("no accessible playlist entries to download")

    out_dir = tmp_path / "test_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = svc.download_video(
        url=first.url,
        outtmpl=str(out_dir / "%(title)s.%(ext)s"),
        quality="best",
        skip_existing=False,
    )
    elapsed = time.time() - started

    with capsys.disabled():
        print("\n===== INTEGRATION: DOWNLOAD =====")
        print(f"video      : {first.title}")
        print(f"status     : {result.status}")
        print(f"filepath   : {result.filepath}")
        print(f"filesize   : {utils.human_bytes(result.filesize)}")
        print(f"error      : {result.error}")
        print(f"elapsed    : {elapsed:.2f}s")
        if result.filepath and Path(result.filepath).exists():
            probe = utils.probe_media(result.filepath, ffprobe_path=svc.ffmpeg.ffprobe)
            print(f"probe ok   : {probe.get('ok')}")
            if probe.get("ok"):
                print(f"duration   : {probe.get('duration')}")
                print(f"format     : {probe.get('format_name')}")
                print(f"video codec: {(probe.get('video') or {}).get('codec_name')}")
                print(f"audio codec: {(probe.get('audio') or {}).get('codec_name')}")
            else:
                print(f"probe error: {probe.get('error')}")
        print("=================================\n")

    assert result.status in ("completed", "skipped"), f"download failed: {result.error}"
    assert result.filepath, "no output file path recorded"
    assert Path(result.filepath).exists(), "output file does not exist"
    assert Path(result.filepath).stat().st_size > 0, "output file is empty"
    assert not result.filepath.endswith(".part")


# --------------------------------------------------------------------------- #
# Optional: FFmpeg merge smoke test.
#
# The supplied playlist only exposes 360p combined streams to the available
# YouTube clients, so it cannot exercise separate video+audio merging. This
# opt-in test uses the CC-licensed "Blender Open Movie" (aqz-KE-bpKQ) purely to
# verify the merge + FFprobe path. It is never run by default.
# --------------------------------------------------------------------------- #

MERGE_TEST_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Blender open movie


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION_MERGE") != "1",
                    reason="set RUN_INTEGRATION_MERGE=1 to run the FFmpeg merge test")
@requires_net
def test_integration_merge_highres(tmp_path, capsys):
    svc = DownloadService()
    if not svc.ffmpeg_available:
        pytest.skip("FFmpeg is required to test merging")

    formats = svc.get_formats(MERGE_TEST_URL)
    separate = [f for f in formats
                if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")]
    if not separate:
        pytest.skip("no separate video-only streams available")

    out_dir = tmp_path / "merge_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = svc.download_video(
        url=MERGE_TEST_URL,
        outtmpl=str(out_dir / "%(title)s.%(ext)s"),
        quality="720",
        skip_existing=False,
    )
    elapsed = time.time() - started
    probe = {}
    if result.filepath and Path(result.filepath).exists():
        probe = utils.probe_media(result.filepath, ffprobe_path=svc.ffmpeg.ffprobe)

    with capsys.disabled():
        print("\n===== INTEGRATION: MERGE =====")
        print(f"status     : {result.status}")
        print(f"filepath   : {result.filepath}")
        print(f"filesize   : {utils.human_bytes(result.filesize)}")
        print(f"error      : {result.error}")
        print(f"elapsed    : {elapsed:.2f}s")
        print(f"probe ok   : {probe.get('ok')}")
        if probe.get("ok"):
            print(f"video codec: {(probe.get('video') or {}).get('codec_name')} "
                  f"{(probe.get('video') or {}).get('height')}p")
            print(f"audio codec: {(probe.get('audio') or {}).get('codec_name')}")
        print("==============================\n")

    assert result.status == "completed", f"merge download failed: {result.error}"
    assert probe.get("ok"), f"FFprobe verification failed: {probe.get('error')}"
    # A successful merge must contain BOTH a video and an audio stream.
    assert probe.get("video"), "merged file has no video stream"
    assert probe.get("audio"), "merged file has no audio stream"


SHORT_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" (19s)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION_MERGE") != "1",
                    reason="set RUN_INTEGRATION_MERGE=1 to run the MP3 conversion test")
@requires_net
def test_integration_audio_mp3(tmp_path, capsys):
    """Download audio-only and convert to MP3 via FFmpeg."""
    svc = DownloadService()
    if not svc.ffmpeg_available:
        pytest.skip("FFmpeg is required for MP3 conversion")

    out_dir = tmp_path / "audio_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = svc.download_video(
        url=SHORT_VIDEO_URL,
        outtmpl=str(out_dir / "%(title)s.%(ext)s"),
        quality="audio_mp3",
        skip_existing=False,
    )
    probe = {}
    if result.filepath and Path(result.filepath).exists():
        probe = utils.probe_media(result.filepath, ffprobe_path=svc.ffmpeg.ffprobe)

    with capsys.disabled():
        print("\n===== INTEGRATION: MP3 =====")
        print(f"status   : {result.status}")
        print(f"filepath : {result.filepath}")
        print(f"filesize : {utils.human_bytes(result.filesize)}")
        print(f"error    : {result.error}")
        print(f"probe ok : {probe.get('ok')}  audio={(probe.get('audio') or {}).get('codec_name')}")
        print("============================\n")

    assert result.status == "completed", f"mp3 download failed: {result.error}"
    assert result.filepath and result.filepath.lower().endswith(".mp3"), \
        "output is not an .mp3 file"
    assert probe.get("ok"), f"FFprobe failed: {probe.get('error')}"
    assert (probe.get("audio") or {}).get("codec_name") == "mp3"


@requires_net
def test_integration_error_handling_missing_video(tmp_path):
    """A nonexistent video id must fail cleanly with a friendly message."""
    svc = DownloadService()
    result = svc.download_video(
        url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
        outtmpl=str(tmp_path / "%(title)s.%(ext)s"),
        quality="best",
    )
    assert result.status == "failed"
    assert result.error
    assert "traceback" not in result.error.lower()
