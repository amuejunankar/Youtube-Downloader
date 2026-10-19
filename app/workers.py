"""Qt threading layer.

The GUI never touches yt-dlp directly. Long running work runs in a
:class:`QObject` moved onto a :class:`QThread`; results are marshalled back to
the GUI thread through Qt signals (queued connections), which is the only
thread-safe way to update widgets.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, Slot

from .downloader import DownloadService, DownloadSummary, YTDLP_AVAILABLE, YTDLP_IMPORT_ERROR


class AnalyzeWorker(QObject):
    """Extracts metadata (video info + playlist entries) off the GUI thread."""

    log = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, url: str, ffmpeg_status=None, playlist_limit: Optional[int] = None):
        super().__init__()
        self._url = url
        self._ffmpeg_status = ffmpeg_status
        self._playlist_limit = playlist_limit

    @Slot()
    def run(self) -> None:
        try:
            if not YTDLP_AVAILABLE:
                self.failed.emit(f"yt-dlp is not installed: {YTDLP_IMPORT_ERROR}")
                return
            service = DownloadService(ffmpeg_status=self._ffmpeg_status, log_cb=self.log.emit)
            self.log.emit(f"Analyzing: {self._url}")
            result = service.analyze(self._url, playlist_limit=self._playlist_limit)
            payload = result.to_dict()
            if result.error and not result.formats and not result.entries:
                self.failed.emit(result.error)
                return
            self.finished.emit(payload)
        except Exception as exc:  # noqa: BLE001 - must never crash the thread
            self.failed.emit(str(exc))


class DownloadWorker(QObject):
    """Runs a batch of downloads sequentially and reports granular progress."""

    log = Signal(str)
    progress = Signal(dict)          # per-video byte progress
    postproc = Signal(dict)          # postprocessor (merge / convert) events
    video_done = Signal(dict)        # one DownloadResult
    finished = Signal(dict)          # DownloadSummary.to_dict()
    failed = Signal(str)

    def __init__(self, payload: Dict[str, Any]):
        super().__init__()
        self._payload = payload
        self._cancel_event = threading.Event()

    # -- public (called from GUI thread) ----------------------------------- #
    def cancel(self) -> None:
        """Request cancellation. Thread-safe (only sets an Event)."""
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # -- internals --------------------------------------------------------- #
    def _cancel_check(self) -> bool:
        return self._cancel_event.is_set()

    @Slot()
    def run(self) -> None:
        try:
            if not YTDLP_AVAILABLE:
                self.failed.emit(f"yt-dlp is not installed: {YTDLP_IMPORT_ERROR}")
                return

            payload = self._payload
            service = DownloadService(
                ffmpeg_status=payload.get("ffmpeg_status"),
                log_cb=self.log.emit,
            )

            tasks: List[Dict[str, Any]] = payload.get("tasks") or []
            if not tasks:
                self.failed.emit("No videos to download.")
                return

            self.log.emit(f"Starting download of {len(tasks)} item(s) at quality '{payload.get('quality')}'.")

            summary: DownloadSummary = service.download_many(
                tasks=tasks,
                output_dir=payload["output_dir"],
                quality=payload.get("quality", "best"),
                make_playlist_folder=payload.get("make_playlist_folder", False),
                playlist_title=payload.get("playlist_title", ""),
                skip_existing=payload.get("skip_existing", True),
                retries=payload.get("retries", 3),
                concurrent_fragments=payload.get("concurrent_fragments", 4),
                embed_metadata=payload.get("embed_metadata", True),
                embed_thumbnail=payload.get("embed_thumbnail", False),
                number_files=payload.get("number_files", False),
                progress_cb=self.progress.emit,
                postproc_cb=self.postproc.emit,
                video_done_cb=lambda r: self.video_done.emit(r.to_dict()),
                cancel_check=self._cancel_check,
            )
            self.finished.emit(summary.to_dict())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
