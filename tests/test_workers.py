"""Tests for the Qt worker layer (:mod:`app.workers`).

These run the workers synchronously (no real QThread) while still exercising
the Qt signal wiring, so a missing/broken signal connection is caught.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.workers as workers  # noqa: E402
from app.downloader import AnalysisResult, DownloadResult, KIND_VIDEO  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_analyze_worker_emits_finished(qt_app, monkeypatch):
    class FakeService:
        def __init__(self, ffmpeg_status=None, log_cb=None):
            self.log_cb = log_cb

        def analyze(self, url, playlist_limit=None):
            res = AnalysisResult(url=url, title="Test", formats=[{"format_id": "18"}])
            res.kind = KIND_VIDEO
            res.entry_count = 0
            return res

    monkeypatch.setattr(workers, "DownloadService", FakeService)
    w = workers.AnalyzeWorker("https://www.youtube.com/watch?v=x")
    got, logs = [], []
    w.finished.connect(got.append)
    w.log.connect(logs.append)
    w.run()
    assert got and got[0]["title"] == "Test"
    assert got[0]["kind"] == KIND_VIDEO
    assert logs


def test_analyze_worker_emits_failed(qt_app, monkeypatch):
    class FakeService:
        def __init__(self, ffmpeg_status=None, log_cb=None):
            pass

        def analyze(self, url, playlist_limit=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(workers, "DownloadService", FakeService)
    w = workers.AnalyzeWorker("u")
    errors = []
    w.failed.connect(errors.append)
    w.run()
    assert errors and "boom" in errors[0]


def test_download_worker_emits_progress_and_finished(qt_app, monkeypatch):
    class FakeSummary:
        def to_dict(self):
            return {"completed": 1, "skipped": 0, "failed": 0, "cancelled": 0,
                    "total": 1, "elapsed": 0.1, "results": [], "cancelled_job": False,
                    "output_dir": "out"}

    class FakeService:
        def __init__(self, ffmpeg_status=None, log_cb=None):
            self.log_cb = log_cb

        def download_many(self, **kw):
            kw["progress_cb"]({"percent": 0.5, "title": "T", "status": "downloading"})
            kw["postproc_cb"]({"postprocessor": "Merger", "status": "finished"})
            result = DownloadResult(url="u", title="T", index=1, status="completed")
            kw["video_done_cb"](result)
            assert kw["cancel_check"]() is False
            return FakeSummary()

    monkeypatch.setattr(workers, "DownloadService", FakeService)
    payload = {
        "tasks": [{"url": "u", "title": "T", "index": 1}],
        "output_dir": "out",
        "quality": "best",
    }
    w = workers.DownloadWorker(payload)
    progress, post, done, finished, errors = [], [], [], [], []
    w.progress.connect(progress.append)
    w.postproc.connect(post.append)
    w.video_done.connect(done.append)
    w.finished.connect(finished.append)
    w.failed.connect(errors.append)
    w.run()

    assert progress and progress[0]["percent"] == 0.5
    assert post and post[0]["postprocessor"] == "Merger"
    assert done and done[0]["status"] == "completed"
    assert finished and finished[0]["completed"] == 1
    assert not errors


def test_download_worker_cancel_flag(qt_app):
    w = workers.DownloadWorker({"tasks": [], "output_dir": "out"})
    assert w.is_cancelled is False
    w.cancel()
    assert w.is_cancelled is True


def test_download_worker_empty_tasks_fails(qt_app):
    w = workers.DownloadWorker({"tasks": [], "output_dir": "out"})
    errors = []
    w.failed.connect(errors.append)
    w.run()
    assert errors and "no videos" in errors[0].lower()
