"""Unit tests for :mod:`app.downloader`.

Network access is fully mocked: a fake ``_TrackingYoutubeDL`` is injected so
no real requests are made.
"""

from __future__ import annotations

import pytest

import app.downloader as downloader
from app.downloader import (
    DownloadResult,
    DownloadService,
    DownloadTracker,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_CANCELLED,
)
from app.utils import FFmpegStatus


def _service():
    return DownloadService(ffmpeg_status=FFmpegStatus())


def make_fake_ydl(monkeypatch, extract_impl):
    """Install a fake YoutubeDL whose extract_info delegates to *extract_impl*."""

    class FakeYDL:
        def __init__(self, opts, tracker):
            self.opts = opts
            self.tracker = tracker

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            return extract_impl(self, url, download)

    monkeypatch.setattr(downloader, "_TrackingYoutubeDL", FakeYDL)
    return FakeYDL


def _outtmpl(tmp_path):
    return str(tmp_path / "%(title)s.%(ext)s")


# --------------------------------------------------------------------------- #
# analyze() - metadata parsing
# --------------------------------------------------------------------------- #

def test_analyze_video(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "extract_video_info", lambda url: {
        "title": "Test Video", "id": "abc", "duration": 212,
        "uploader": "Someone", "formats": [{"format_id": "18", "height": 360,
                                             "vcodec": "avc1", "acodec": "mp4a"}],
    })
    res = svc.analyze("https://www.youtube.com/watch?v=abc")
    assert res.kind == downloader.KIND_VIDEO
    assert res.title == "Test Video"
    assert res.duration == 212
    assert res.formats and res.formats[0]["format_id"] == "18"
    assert res.error is None


def test_analyze_playlist(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "extract_playlist_info", lambda url, limit=None: {
        "title": "My Playlist",
        "entries": [
            {"id": "a", "title": "A", "url": "https://www.youtube.com/watch?v=a", "duration": 10},
            {"id": "b", "title": "B", "url": "https://www.youtube.com/watch?v=b", "duration": 20},
        ],
    })
    res = svc.analyze("https://www.youtube.com/playlist?list=PL123")
    assert res.kind == downloader.KIND_PLAYLIST
    assert res.entry_count == 2
    assert res.playlist_title == "My Playlist"
    assert [e.title for e in res.entries] == ["A", "B"]


def test_analyze_video_in_playlist(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "extract_video_info", lambda url: {
        "title": "Vid", "id": "v", "formats": [{"format_id": "18"}],
    })
    monkeypatch.setattr(svc, "extract_playlist_info", lambda url, limit=None: {
        "title": "PL", "entries": [{"id": "v", "title": "Vid",
                                    "url": "https://www.youtube.com/watch?v=v"}],
    })
    res = svc.analyze("https://www.youtube.com/watch?v=v&list=PL1")
    assert res.kind == downloader.KIND_VIDEO_IN_PLAYLIST
    assert res.has_video and res.is_playlist
    assert res.entry_count == 1


def test_analyze_invalid_url():
    res = _service().analyze("https://example.com/x")
    assert res.error
    assert res.kind == downloader.KIND_INVALID


def test_analyze_video_error_reports(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "extract_video_info",
                        lambda url: (_ for _ in ()).throw(downloader.DownloadError("Video unavailable")))
    res = svc.analyze("https://www.youtube.com/watch?v=dead")
    assert res.error
    assert "unavailable" in res.error.lower()


def test_parse_entries_marks_unavailable():
    pinfo = {"entries": [
        {"id": "a", "title": "Good", "url": "https://y/watch?v=a", "duration": 5},
        None,
        {"id": "b", "title": "[Private video]", "url": None},
    ]}
    entries = DownloadService._parse_entries(pinfo)
    assert len(entries) == 3
    assert entries[0].available is True
    assert entries[1].available is False
    assert entries[2].available is False
    # URL is synthesised from the id when missing.
    assert entries[2].url == "https://www.youtube.com/watch?v=b"
    # An explicit URL is preserved as-is.
    assert entries[0].url == "https://y/watch?v=a"


# --------------------------------------------------------------------------- #
# download_video() - success / skip / failure / cancel
# --------------------------------------------------------------------------- #

def test_download_video_completed(tmp_path, monkeypatch):
    out = tmp_path / "video.mp4"
    events = []

    def impl(self, url, download):
        self.tracker.progress_hook({
            "status": "downloading", "filename": str(out),
            "downloaded_bytes": 50, "total_bytes": 100,
            "speed": 1024.0, "eta": 1, "info_dict": {"title": "T"},
        })
        out.write_bytes(b"x" * 100)
        self.tracker.progress_hook({
            "status": "finished", "filename": str(out),
            "downloaded_bytes": 100, "total_bytes": 100, "info_dict": {"title": "T"},
        })
        return {"filepath": str(out), "title": "T"}

    make_fake_ydl(monkeypatch, impl)
    svc = _service()
    result = svc.download_video(
        url="https://www.youtube.com/watch?v=x",
        outtmpl=_outtmpl(tmp_path), quality="best",
        progress_cb=events.append,
    )
    assert result.status == STATUS_COMPLETED
    assert result.filepath == str(out)
    assert result.filesize == 100
    assert events and events[0]["status"] == "downloading"


def test_download_video_skipped_when_file_exists(tmp_path, monkeypatch):
    out = tmp_path / "existing.mp4"
    out.write_bytes(b"y" * 42)

    def impl(self, url, download):
        self.tracker.on_video_skipped({"title": "T"}, str(out))
        return {"title": "T"}

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="https://www.youtube.com/watch?v=x",
        outtmpl=_outtmpl(tmp_path), quality="best", skip_existing=True,
    )
    assert result.status == STATUS_SKIPPED
    assert result.filepath == str(out)


def test_download_video_skipped_has_priority(tmp_path, monkeypatch):
    """A skipped result must not be overwritten by later logic."""
    out = tmp_path / "f.mp4"
    out.write_bytes(b"z" * 10)

    def impl(self, url, download):
        self.tracker.on_video_skipped({"title": "T"}, str(out))
        return {}

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert result.status == STATUS_SKIPPED


def test_download_video_failure(tmp_path, monkeypatch):
    def impl(self, url, download):
        raise downloader.DownloadError("ERROR: Video unavailable")

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert result.status == STATUS_FAILED
    assert "unavailable" in (result.error or "").lower()


def test_download_video_cancelled(tmp_path, monkeypatch):
    state = {"cancel": False}

    def impl(self, url, download):
        state["cancel"] = True  # user cancels mid-download
        self.tracker.progress_hook({
            "status": "downloading", "downloaded_bytes": 1, "total_bytes": 10,
            "info_dict": {},
        })
        return {}

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
        cancel_check=lambda: state["cancel"],
    )
    assert result.status == STATUS_CANCELLED


def test_download_video_no_media_fails(tmp_path, monkeypatch):
    def impl(self, url, download):
        return {"title": "T"}  # no hooks fired -> nothing downloaded

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert result.status == STATUS_FAILED
    assert "no media" in (result.error or "").lower()


# --------------------------------------------------------------------------- #
# download_many() - task orchestration, numbering, cancellation
# --------------------------------------------------------------------------- #

def _fake_ok_result(url, title, index, tmp_path):
    f = tmp_path / f"out_{index}.mp4"
    f.write_bytes(b"x" * 10)
    return DownloadResult(url=url, title=title, index=index,
                          status=STATUS_COMPLETED, filepath=str(f), filesize=10)


def test_download_many_numbers_playlist_files(tmp_path, monkeypatch):
    svc = _service()
    calls = []

    def fake_download_video(**kwargs):
        calls.append(kwargs)
        return _fake_ok_result(kwargs["url"], kwargs.get("title_hint", ""),
                               kwargs.get("index"), tmp_path)

    monkeypatch.setattr(svc, "download_video", fake_download_video)
    tasks = [
        {"url": "https://y/watch?v=a", "title": "A", "index": 1},
        {"url": "https://y/watch?v=b", "title": "B", "index": 2},
    ]
    summary = svc.download_many(tasks=tasks, output_dir=str(tmp_path),
                                quality="best", number_files=True)
    assert len(summary.results) == 2
    assert len(summary.completed) == 2
    assert calls[0]["outtmpl"].endswith("001 - %(title)s.%(ext)s")
    assert calls[1]["outtmpl"].endswith("002 - %(title)s.%(ext)s")


def test_download_many_single_video_no_numbering(tmp_path, monkeypatch):
    svc = _service()
    calls = []
    monkeypatch.setattr(svc, "download_video",
                        lambda **kw: (calls.append(kw) or _fake_ok_result("u", "T", None, tmp_path)))
    svc.download_many(tasks=[{"url": "u", "title": "T", "index": None}],
                      output_dir=str(tmp_path), quality="best", number_files=False)
    assert calls[0]["outtmpl"].endswith("%(title)s.%(ext)s")


def test_download_many_playlist_folder(tmp_path, monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "download_video",
                        lambda **kw: _fake_ok_result("u", "T", 1, tmp_path))
    summary = svc.download_many(
        tasks=[{"url": "u", "title": "T", "index": 1}],
        output_dir=str(tmp_path), quality="best",
        make_playlist_folder=True, playlist_title="My: Playlist?",
    )
    assert "My_ Playlist_" in summary.output_dir


def test_download_many_cancellation_marks_remaining(tmp_path, monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "download_video",
                        lambda **kw: _fake_ok_result(kw["url"], "T", kw.get("index"), tmp_path))
    checks = {"n": 0}

    def cancel_check():
        checks["n"] += 1
        return checks["n"] > 1  # cancel before the 2nd task

    tasks = [{"url": f"u{i}", "title": f"T{i}", "index": i} for i in (1, 2, 3)]
    summary = svc.download_many(tasks=tasks, output_dir=str(tmp_path),
                                quality="best", cancel_check=cancel_check)
    assert summary.cancelled is True
    assert len(summary.completed) == 1
    assert len(summary.cancelled_results) == 2
    assert len(summary.results) == 3  # every task has a recorded outcome


def test_download_many_tracks_failures(tmp_path, monkeypatch):
    svc = _service()

    def fake_download_video(**kwargs):
        status = STATUS_COMPLETED if kwargs.get("index") == 1 else STATUS_FAILED
        return DownloadResult(url=kwargs["url"], title="T", index=kwargs.get("index"),
                              status=status, error=None if status == STATUS_COMPLETED else "boom")

    monkeypatch.setattr(svc, "download_video", fake_download_video)
    summary = svc.download_many(
        tasks=[{"url": "a", "title": "T", "index": 1}, {"url": "b", "title": "T", "index": 2}],
        output_dir=str(tmp_path), quality="best",
    )
    assert len(summary.completed) == 1
    assert len(summary.failed) == 1


# --------------------------------------------------------------------------- #
# DownloadTracker / progress hooks
# --------------------------------------------------------------------------- #

def test_progress_hook_handles_unknown_size():
    captured = []
    t = DownloadTracker(index=1, title_hint="x", url="u", quality="best",
                        skip_existing=True, progress_cb=captured.append)
    t.progress_hook({"status": "downloading", "downloaded_bytes": 10,
                     "total_bytes": None, "info_dict": {}})
    assert captured and captured[0]["percent"] is None
    assert captured[0]["total_bytes"] is None


def test_progress_hook_computes_percent():
    captured = []
    t = DownloadTracker(index=1, title_hint="x", url="u", quality="best",
                        skip_existing=True, progress_cb=captured.append)
    t.progress_hook({"status": "downloading", "downloaded_bytes": 25,
                     "total_bytes": 100, "info_dict": {}})
    assert captured[0]["percent"] == pytest.approx(0.25)


def test_progress_hook_finished_sets_full():
    captured = []
    t = DownloadTracker(index=1, title_hint="x", url="u", quality="best",
                        skip_existing=True, progress_cb=captured.append)
    t.progress_hook({"status": "finished", "downloaded_bytes": 100,
                     "total_bytes": 100, "info_dict": {}})
    assert captured[0]["percent"] == 1.0


def test_tracker_postprocessor_hook_records_file(tmp_path):
    f = tmp_path / "merged.mp4"
    f.write_bytes(b"x" * 5)
    t = DownloadTracker(index=1, title_hint="x", url="u", quality="best",
                        skip_existing=False)
    t.postprocessor_hook({"postprocessor": "Merger", "status": "finished",
                          "info_dict": {"filepath": str(f)}})
    assert t.final_filepath == str(f)
    assert t.resolve_filepath() == str(f)


# --------------------------------------------------------------------------- #
# Error message mapping
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,needle", [
    ("ERROR: Video unavailable", "unavailable"),
    ("ERROR: Private video. Sign in if you've been granted access", "private"),
    ("HTTP Error 429: Too Many Requests", "rate limit"),
    ("HTTP Error 403: Forbidden", "denied"),
    ("Unable to download webpage: getaddrinfo failed", "network"),
    ("This video is only available to Music Premium members (members-only)", "members"),
    ("No space left on device", "space"),
])
def test_friendly_error(raw, needle):
    msg = downloader._friendly_error(Exception(raw)).lower()
    assert needle in msg


def test_friendly_error_truncates_unknown():
    msg = downloader._friendly_error(Exception("z" * 1000))
    assert len(msg) <= 400


def test_looks_unavailable():
    assert downloader._looks_unavailable("[Private video]", {}) is True
    assert downloader._looks_unavailable("Normal Song", {}) is False
    assert downloader._looks_unavailable("x", {"availability": "private"}) is True


def test_unavailable_mapping_via_parse():
    entries = DownloadService._parse_entries({"entries": [
        {"id": "1", "title": "Deleted video", "url": None},
    ]})
    assert entries[0].available is False
