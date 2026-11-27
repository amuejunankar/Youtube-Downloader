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


def test_download_video_succeeds_when_only_finished_hook_fires(tmp_path, monkeypatch):
    """A real download must not be reported as failed just because no
    ``downloading`` progress event was emitted (e.g. resumed files)."""
    out = tmp_path / "resumed.mp4"

    def impl(self, url, download):
        out.write_bytes(b"x" * 64)
        self.tracker.progress_hook({
            "status": "finished", "filename": str(out),
            "downloaded_bytes": 64, "total_bytes": 64, "info_dict": {"title": "T"},
        })
        return {}

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert result.status == STATUS_COMPLETED
    assert result.filepath == str(out)
    assert result.filesize == 64


def test_download_video_finds_mp3_after_conversion(tmp_path, monkeypatch):
    """yt-dlp renames the file during postprocessing; the result must point at
    the .mp3 that actually landed in the output folder."""
    mp3 = tmp_path / "Song [abc].mp3"

    def impl(self, url, download):
        mp3.write_bytes(b"a" * 128)
        return {"title": "Song"}

    make_fake_ydl(monkeypatch, impl)
    ffmpeg = FFmpegStatus(
        ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe",
        ffmpeg_version="ffmpeg version 9.0",
        ffprobe_version="ffprobe version 9.0",
    )
    result = DownloadService(ffmpeg_status=ffmpeg).download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="audio_mp3",
    )
    assert result.status == STATUS_COMPLETED
    assert result.filepath == str(mp3)
    assert result.filesize == 128


def _working_ffmpeg():
    return FFmpegStatus(
        ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe",
        ffmpeg_version="ffmpeg version 9.0",
        ffprobe_version="ffprobe version 9.0",
    )


def test_present_but_unrunnable_ffmpeg_is_not_trusted(tmp_path):
    """A file called ffmpeg that cannot run must not enable merging."""
    ffmpeg = FFmpegStatus(ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe")
    svc = DownloadService(ffmpeg_status=ffmpeg)
    assert svc.ffmpeg_available is False
    assert "ffmpeg_location" not in svc.base_options()
    tracker = DownloadTracker(
        index=1, title_hint="t", url="u", quality="best", skip_existing=False,
    )
    opts = svc._download_options(
        outtmpl=_outtmpl(tmp_path), quality="best", skip_existing=False, retries=1,
        concurrent_fragments=1, embed_metadata=True, embed_thumbnail=False,
        tracker=tracker,
    )
    # Single-file selector only: no "video+audio" merge that needs FFmpeg.
    assert "+" not in opts["format"]
    assert opts["postprocessors"] == []
    assert "merge_output_format" not in opts


def test_progressive_attempt_needs_no_ffmpeg(tmp_path):
    svc = DownloadService(ffmpeg_status=_working_ffmpeg())
    tracker = DownloadTracker(
        index=1, title_hint="t", url="u", quality="best", skip_existing=False,
    )
    opts = svc._download_options(
        outtmpl=_outtmpl(tmp_path), quality="best", skip_existing=False, retries=1,
        concurrent_fragments=1, embed_metadata=True, embed_thumbnail=False,
        tracker=tracker, progressive=True,
    )
    assert opts["postprocessors"] == []
    assert "merge_output_format" not in opts
    assert "+" not in opts["format"]


def test_mp3_keeps_its_encoder_on_the_single_file_route(tmp_path):
    """MP3 still has to be encoded, so that postprocessor is never dropped."""
    svc = DownloadService(ffmpeg_status=_working_ffmpeg())
    tracker = DownloadTracker(
        index=1, title_hint="t", url="u", quality="audio_mp3", skip_existing=False,
    )
    opts = svc._download_options(
        outtmpl=_outtmpl(tmp_path), quality="audio_mp3", skip_existing=False,
        retries=1, concurrent_fragments=1, embed_metadata=True,
        embed_thumbnail=False, tracker=tracker, progressive=True,
    )
    assert [p["key"] for p in opts["postprocessors"]][0] == "FFmpegExtractAudio"


def test_ffmpeg_failure_falls_back_to_single_file(tmp_path, monkeypatch):
    """An FFmpeg merge failure must end with a plain single-file download."""
    out = tmp_path / "video.mp4"
    calls = {"n": 0}
    seen_postprocessors = []

    def impl(self, url, download):
        seen_postprocessors.append(self.opts.get("postprocessors"))
        calls["n"] += 1
        if calls["n"] < 4:
            raise downloader.DownloadError(
                "Postprocessing: ffmpeg exited with code 1"
            )
        out.write_bytes(b"x" * 100)
        self.tracker.progress_hook({
            "status": "finished", "filename": str(out),
            "downloaded_bytes": 100, "total_bytes": 100, "info_dict": {},
        })
        return {"title": "Video"}

    make_fake_ydl(monkeypatch, impl)
    result = DownloadService(ffmpeg_status=_working_ffmpeg()).download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert result.status == STATUS_COMPLETED
    assert seen_postprocessors[-1] == []


def test_download_video_mp3_requires_ffmpeg(tmp_path, monkeypatch):
    make_fake_ydl(monkeypatch, lambda self, url, download: {})
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="audio_mp3",
    )
    assert result.status == STATUS_FAILED
    assert "ffmpeg" in (result.error or "").lower()


def test_download_video_retries_with_clean_partial(tmp_path, monkeypatch):
    """A 403 must retry with another client after clearing the stale .part."""
    out = tmp_path / "video.mp4"
    part = tmp_path / "video.mp4.part"
    attempts = {"n": 0}

    def impl(self, url, download):
        attempts["n"] += 1
        if attempts["n"] == 1:
            part.write_bytes(b"stale")
            self.tracker.progress_hook({
                "status": "downloading", "filename": str(out),
                "downloaded_bytes": 5, "total_bytes": 100, "info_dict": {},
            })
            raise downloader.DownloadError("HTTP Error 403: Forbidden")
        out.write_bytes(b"x" * 100)
        self.tracker.progress_hook({
            "status": "finished", "filename": str(out),
            "downloaded_bytes": 100, "total_bytes": 100, "info_dict": {},
        })
        return {}

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert attempts["n"] == 2
    assert not part.exists()
    assert result.status == STATUS_COMPLETED


def test_download_video_disk_full_fails_without_retrying(tmp_path, monkeypatch):
    """A full disk is not a YouTube problem: report it once, not three times."""
    attempts = {"n": 0}

    def impl(self, url, download):
        attempts["n"] += 1
        # yt-dlp wraps write failures in "unable to download webpage", which
        # used to be reported as a connection problem.
        raise downloader.DownloadError(
            "ERROR: unable to download webpage: [Errno 28] No space left on device"
        )

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert attempts["n"] == 1
    assert result.status == STATUS_FAILED
    assert "out of space" in (result.error or "").lower()
    assert "download methods" not in (result.error or "").lower()


def test_download_video_permission_denied_fails_without_retrying(tmp_path, monkeypatch):
    attempts = {"n": 0}

    def impl(self, url, download):
        attempts["n"] += 1
        raise PermissionError(13, "Permission denied", str(tmp_path / "video.mp4"))

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert attempts["n"] == 1
    assert result.status == STATUS_FAILED
    assert "permission denied" in (result.error or "").lower()


def test_download_video_bad_url_fails_without_retrying(tmp_path, monkeypatch):
    attempts = {"n": 0}

    def impl(self, url, download):
        attempts["n"] += 1
        raise downloader.DownloadError("ERROR: Unsupported URL: https://example.com")

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert attempts["n"] == 1
    assert result.status == STATUS_FAILED
    assert "not supported" in (result.error or "").lower()


def test_download_video_network_error_tries_every_method(tmp_path, monkeypatch):
    """A network failure is exactly what the other methods are for."""
    attempts = {"n": 0}

    def impl(self, url, download):
        attempts["n"] += 1
        raise downloader.DownloadError(
            "ERROR: unable to download webpage: <urlopen error "
            "[Errno 104] Connection reset by peer>"
        )

    make_fake_ydl(monkeypatch, impl)
    result = _service().download_video(
        url="u", outtmpl=_outtmpl(tmp_path), quality="best",
    )
    assert attempts["n"] == 4
    assert result.status == STATUS_FAILED
    assert "4 download methods failed" in (result.error or "").lower()
    assert "network" in (result.error or "").lower()


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
    assert calls[0]["outtmpl"].endswith("001 - %(title).120B [%(id)s].%(ext)s")
    assert calls[1]["outtmpl"].endswith("002 - %(title).120B [%(id)s].%(ext)s")


def test_download_many_single_video_no_numbering(tmp_path, monkeypatch):
    svc = _service()
    calls = []
    monkeypatch.setattr(svc, "download_video",
                        lambda **kw: (calls.append(kw) or _fake_ok_result("u", "T", None, tmp_path)))
    svc.download_many(tasks=[{"url": "u", "title": "T", "index": None}],
                      output_dir=str(tmp_path), quality="best", number_files=False)
    assert calls[0]["outtmpl"].endswith("%(title).120B [%(id)s].%(ext)s")


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


def test_existing_output_detects_merged_file(tmp_path):
    """Skip detection must find the merged file, not the pre-merge name."""
    merged = tmp_path / "Song [abc].mp4"
    merged.write_bytes(b"x" * 10)
    t = DownloadTracker(index=1, title_hint="Song", url="u", quality="best",
                        skip_existing=True)
    assert t.existing_output(str(tmp_path / "Song [abc].webm")) == str(merged)


def test_existing_output_detects_converted_mp3(tmp_path):
    mp3 = tmp_path / "Song [abc].mp3"
    mp3.write_bytes(b"x" * 10)
    t = DownloadTracker(index=1, title_hint="Song", url="u", quality="audio_mp3",
                        skip_existing=True)
    assert t.existing_output(str(tmp_path / "Song [abc].webm")) == str(mp3)


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
    ("Requested format is not available. Use --list-formats", "format"),
    ("Sign in to confirm you're not a bot", "sign-in check"),
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


# --------------------------------------------------------------------------- #
# JavaScript runtime wiring
# --------------------------------------------------------------------------- #

def test_base_options_uses_detected_js_runtime(monkeypatch):
    monkeypatch.setattr(
        downloader, "detect_js_runtime",
        lambda: downloader.JsRuntime("quickjs", "/opt/qjs"),
    )
    opts = downloader.DownloadService(ffmpeg_status=FFmpegStatus()).base_options()
    assert opts["js_runtimes"] == {"quickjs": {"path": "/opt/qjs"}}


def test_base_options_omits_js_runtime_when_missing(monkeypatch):
    monkeypatch.setattr(downloader, "detect_js_runtime", lambda: None)
    opts = downloader.DownloadService(ffmpeg_status=FFmpegStatus()).base_options()
    assert "js_runtimes" not in opts


def test_detect_js_runtime_prefers_bundled_binary(tmp_path, monkeypatch):
    import app.utils as app_utils

    for name in ("qjs", "qjs.exe"):
        binary = tmp_path / name
        binary.write_bytes(b"")
        binary.chmod(0o755)
    monkeypatch.setattr(app_utils, "_runtime_dirs", lambda: [tmp_path])
    monkeypatch.setattr(app_utils.shutil, "which", lambda name: "/usr/bin/deno")

    runtime = app_utils.detect_js_runtime()
    assert runtime is not None
    assert runtime.name == "quickjs"
    assert runtime.path.startswith(str(tmp_path))


def test_detect_js_runtime_falls_back_to_path(tmp_path, monkeypatch):
    import app.utils as app_utils

    monkeypatch.setattr(app_utils, "_runtime_dirs", lambda: [tmp_path])
    monkeypatch.setattr(
        app_utils.shutil, "which",
        lambda name: "/usr/bin/deno" if name == "deno" else None,
    )

    runtime = app_utils.detect_js_runtime()
    assert runtime is not None
    assert runtime.name == "deno"
    assert runtime.path == "/usr/bin/deno"
