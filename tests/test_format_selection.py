"""Unit tests for :mod:`app.format_service` (format discovery & selection)."""

from __future__ import annotations

import pytest

from app import format_service as fs


# A representative slice of a real yt-dlp formats list.
FORMATS = [
    {"format_id": "137", "ext": "mp4", "height": 1080, "width": 1920, "fps": 30,
     "vcodec": "avc1.640028", "acodec": "none", "tbr": 4000, "filesize": 50_000_000},
    {"format_id": "136", "ext": "mp4", "height": 720, "width": 1280, "fps": 30,
     "vcodec": "avc1.4d401f", "acodec": "none", "tbr": 2500, "filesize": 25_000_000},
    {"format_id": "18", "ext": "mp4", "height": 360, "width": 640, "fps": 30,
     "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "tbr": 700, "filesize": 8_000_000},
    {"format_id": "140", "ext": "m4a", "height": None, "width": None, "fps": None,
     "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "filesize": 3_000_000},
    {"format_id": "251", "ext": "webm", "height": None, "width": None, "fps": None,
     "vcodec": "none", "acodec": "opus", "abr": 130, "filesize": 3_100_000},
]


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #

def test_available_video_heights_descending():
    assert fs.available_video_heights(FORMATS) == [1080, 720, 360]


def test_available_video_heights_empty():
    audio_only = [f for f in FORMATS if not fs.is_video(f)]
    assert fs.available_video_heights(audio_only) == []
    assert fs.available_video_heights(None) == []


def test_has_combined_format():
    assert fs.has_combined_format(FORMATS) is True
    assert fs.has_combined_format([f for f in FORMATS if not fs.has_both(f)]) is False


def test_summarize_format():
    s = fs.summarize_format(FORMATS[0])
    assert s["resolution"] == "1920x1080"
    assert s["height"] == 1080
    assert s["fps"] == 30
    assert s["has_video"] and not s["has_audio"]
    assert s["filesize"] == 50_000_000


def test_summarize_audio_format_resolution_label():
    s = fs.summarize_format(FORMATS[3])
    assert s["has_audio"] and not s["has_video"]
    assert s["resolution"] == "audio"


def test_format_size_prefers_exact():
    assert fs.format_size({"filesize": 10, "filesize_approx": 99}) == 10
    assert fs.format_size({"filesize_approx": 99}) == 99
    assert fs.format_size({}) is None


# --------------------------------------------------------------------------- #
# Quality options
# --------------------------------------------------------------------------- #

def test_available_quality_options_reflects_real_heights():
    options = fs.available_quality_options(FORMATS, ffmpeg_available=True)
    keys = [k for k, _ in options]
    assert fs.QUALITY_BEST in keys
    assert "1080" in keys and "720" in keys and "360" in keys
    assert "2160" not in keys  # not present in the formats
    assert fs.QUALITY_AUDIO in keys
    assert fs.QUALITY_AUDIO_MP3 in keys


def test_available_quality_options_hides_mp3_without_ffmpeg():
    keys = [k for k, _ in fs.available_quality_options(FORMATS, ffmpeg_available=False)]
    assert fs.QUALITY_AUDIO_MP3 not in keys
    assert fs.QUALITY_AUDIO in keys


def test_height_label():
    assert fs.height_label(2160) == "2160p (4K)"
    assert fs.height_label(999) == "999p"


# --------------------------------------------------------------------------- #
# Format selector strings
# --------------------------------------------------------------------------- #

def test_format_selector_best_with_ffmpeg():
    assert fs.format_selector(fs.QUALITY_BEST, True) == "bestvideo+bestaudio/best"


def test_format_selector_best_without_ffmpeg():
    assert fs.format_selector(fs.QUALITY_BEST, False) == "best[acodec!=none][vcodec!=none]/best"


def test_format_selector_height_with_ffmpeg():
    sel = fs.format_selector("1080", True)
    assert "bestvideo[height<=1080]+bestaudio" in sel
    assert "best[height<=1080]" in sel


def test_format_selector_height_without_ffmpeg_uses_combined():
    sel = fs.format_selector("720", False)
    assert sel.startswith("best[height<=720][acodec!=none][vcodec!=none]")
    assert "bestvideo" not in sel


def test_format_selector_audio():
    assert fs.format_selector(fs.QUALITY_AUDIO, True) == "bestaudio/best"
    assert fs.format_selector(fs.QUALITY_AUDIO_MP3, True) == "bestaudio/best"


# --------------------------------------------------------------------------- #
# Resolution resolution / fallback reporting
# --------------------------------------------------------------------------- #

def test_resolve_format_picks_best_at_or_below():
    r = fs.resolve_format("720", FORMATS, ffmpeg_available=True)
    assert r.video["height"] == 720
    assert r.audio is not None
    assert r.needs_merge is True  # 720p is video-only + separate audio
    assert r.fallback is False


def test_resolve_format_exact_height_no_fallback():
    r = fs.resolve_format("1080", FORMATS, ffmpeg_available=True)
    assert r.video["height"] == 1080
    assert r.fallback is False


def test_resolve_format_reports_fallback_when_unavailable():
    r = fs.resolve_format("2160", FORMATS, ffmpeg_available=True)
    assert r.video["height"] == 1080
    assert r.fallback is True
    assert "not available" in r.note.lower()
    assert "1080p" in r.note


def test_resolve_format_without_ffmpeg_uses_combined():
    r = fs.resolve_format("720", FORMATS, ffmpeg_available=False)
    # No 720p combined format exists -> best combined is 360p (id 18).
    assert r.video["format_id"] == "18"
    assert r.fallback is True
    assert r.needs_merge is False


def test_resolve_format_best_without_ffmpeg():
    r = fs.resolve_format(fs.QUALITY_BEST, FORMATS, ffmpeg_available=False)
    assert r.video["format_id"] == "18"  # the only combined format


def test_resolve_format_audio():
    r = fs.resolve_format(fs.QUALITY_AUDIO, FORMATS, ffmpeg_available=True)
    assert r.audio is not None
    assert r.audio["format_id"] in ("251", "140")


def test_resolve_format_no_formats():
    r = fs.resolve_format("1080", [], ffmpeg_available=True)
    assert r.video is None
    assert r.note


# --------------------------------------------------------------------------- #
# Postprocessors
# --------------------------------------------------------------------------- #

def test_build_postprocessors_mp3_requires_ffmpeg():
    pps = fs.build_postprocessors(fs.QUALITY_AUDIO_MP3, ffmpeg_available=True)
    assert any(p["key"] == "FFmpegExtractAudio" for p in pps)
    assert fs.build_postprocessors(fs.QUALITY_AUDIO_MP3, ffmpeg_available=False) == []


def test_build_postprocessors_metadata_toggle():
    pps = fs.build_postprocessors(fs.QUALITY_BEST, True, embed_metadata=True)
    assert any(p["key"] == "FFmpegMetadata" for p in pps)
    pps2 = fs.build_postprocessors(fs.QUALITY_BEST, True, embed_metadata=False)
    assert not any(p["key"] == "FFmpegMetadata" for p in pps2)


def test_target_height():
    assert fs.target_height("1080") == 1080
    assert fs.target_height(fs.QUALITY_BEST) is None
    assert fs.target_height("nonsense") is None
