"""Unit tests for :mod:`app.utils` (no network, no Qt)."""

from __future__ import annotations

import os

import pytest

from app import utils
from app.settings import DEFAULTS, Settings


# --------------------------------------------------------------------------- #
# URL classification / validation
# --------------------------------------------------------------------------- #

def test_classify_video():
    assert utils.classify_url("https://www.youtube.com/watch?v=CUJcKAF5wG8") == utils.KIND_VIDEO


def test_classify_playlist():
    assert utils.classify_url(
        "https://www.youtube.com/playlist?list=PLV-cxl3VSwWE_ocGSxzOhIrLp4X1cDcxk"
    ) == utils.KIND_PLAYLIST


def test_classify_video_in_playlist():
    assert utils.classify_url(utils.DEFAULT_TEST_URL) == utils.KIND_VIDEO_IN_PLAYLIST


def test_classify_short_url():
    assert utils.classify_url("https://youtu.be/CUJcKAF5wG8") == utils.KIND_VIDEO


def test_classify_invalid_hosts():
    assert utils.classify_url("https://example.com/watch?v=x") == utils.KIND_INVALID
    assert utils.classify_url("") == utils.KIND_INVALID
    assert utils.classify_url("not a url") == utils.KIND_INVALID


def test_extract_ids():
    assert utils.extract_video_id(utils.DEFAULT_TEST_URL) == "CUJcKAF5wG8"
    assert utils.extract_playlist_id(utils.DEFAULT_TEST_URL) == "PLV-cxl3VSwWE_ocGSxzOhIrLp4X1cDcxk"
    assert utils.extract_video_id("https://youtu.be/abc123") == "abc123"


def test_normalize_url_strips_quotes_and_space():
    assert utils.normalize_url('  "https://youtu.be/x"  ') == "https://youtu.be/x"


@pytest.mark.parametrize("url", [
    "",
    "   ",
    "not a url",
    "ftp://youtube.com/watch?v=x",
    "https://example.com/watch?v=x",
    "https://www.youtube.com/",
])
def test_validate_url_rejects_bad(url):
    ok, err = utils.validate_url(url)
    assert ok is False
    assert err


def test_validate_url_accepts_good():
    ok, err = utils.validate_url(utils.DEFAULT_TEST_URL)
    assert ok is True
    assert err is None


# --------------------------------------------------------------------------- #
# Filename sanitisation
# --------------------------------------------------------------------------- #

def test_sanitize_removes_illegal_chars():
    assert utils.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_empty():
    assert utils.sanitize_filename("   ") == "untitled"
    assert utils.sanitize_filename(None) == "untitled"


def test_sanitize_reserved_name():
    assert utils.sanitize_filename("CON") == "CON_"
    assert utils.sanitize_filename("nul") == "nul_"


def test_sanitize_strips_trailing_dots_and_spaces():
    assert utils.sanitize_filename("trailing...  ") == "trailing"


def test_sanitize_truncates_long_names():
    assert len(utils.sanitize_filename("a" * 500)) == 150


def test_sanitize_collapses_repeats():
    assert "__" not in utils.sanitize_filename("a///b")


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    (None, "—"),
    (0, "0 B"),
    (1024, "1.0 KiB"),
    (1536, "1.5 KiB"),
    (1048576, "1.0 MiB"),
])
def test_human_bytes(value, expected):
    assert utils.human_bytes(value) == expected


@pytest.mark.parametrize("value,expected", [
    (None, "—"),
    (0, "0:00"),
    (65, "1:05"),
    (3661, "1:01:01"),
])
def test_human_duration(value, expected):
    assert utils.human_duration(value) == expected


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #

def test_unique_path(tmp_path):
    target = tmp_path / "file.mp4"
    assert utils.unique_path(target) == target
    target.write_text("x")
    assert utils.unique_path(target) == tmp_path / "file (1).mp4"


def test_ensure_dir_and_writable(tmp_path):
    d = tmp_path / "a" / "b"
    utils.ensure_dir(d)
    assert d.is_dir()
    assert utils.is_writable_dir(d) is True
    assert utils.is_writable_dir(tmp_path / "missing") is False


def test_default_output_dir_points_under_home():
    p = utils.default_output_dir()
    assert "YouTube" in p


# --------------------------------------------------------------------------- #
# FFmpeg detection (mocked)
# --------------------------------------------------------------------------- #

def test_detect_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(utils, "find_executable", lambda name, extra=None: None)
    st = utils.detect_ffmpeg()
    assert st.available is False
    assert st.partial is False
    assert "not found" in st.describe().lower()


def test_detect_ffmpeg_partial(monkeypatch):
    monkeypatch.setattr(utils, "find_executable",
                        lambda name, extra=None: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(utils, "_run_version", lambda exe, timeout=10: None)
    st = utils.detect_ffmpeg()
    assert st.partial is True
    assert st.available is False


def test_detect_ffmpeg_available(monkeypatch):
    monkeypatch.setattr(utils, "find_executable", lambda name, extra=None: name)
    monkeypatch.setattr(utils, "_run_version", lambda exe, timeout=10: f"{exe} version 1.0")
    st = utils.detect_ffmpeg()
    assert st.available is True
    assert st.ffmpeg and st.ffprobe


def test_probe_media_missing_file(tmp_path):
    res = utils.probe_media(tmp_path / "nope.mp4")
    assert res["ok"] is False
    assert res["error"]


def test_probe_media_empty_file(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    res = utils.probe_media(f)
    assert res["ok"] is False
    assert "empty" in res["error"].lower()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    assert s.get("quality") == DEFAULTS["quality"]
    assert s.get("output_dir")  # default resolved
    s.set("quality", "1080")
    s.set("output_dir", str(tmp_path))
    assert s.save() is True

    s2 = Settings(path)
    assert s2.get("quality") == "1080"
    assert s2.get("output_dir") == str(tmp_path)


def test_settings_corrupt_file_is_safe(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json ")
    s = Settings(path)
    assert s.get("quality") == DEFAULTS["quality"]


def test_settings_unknown_key_default():
    s = Settings.__new__(Settings)
    s._path = type("P", (), {})()  # type: ignore[assignment]
    s._data = dict(DEFAULTS)
    assert s.get("does_not_exist", "fallback") == "fallback"
