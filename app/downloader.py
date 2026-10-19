"""yt-dlp based download engine.

Everything in this module is Qt-free so it can be unit tested and safely run
from a worker thread. Progress is reported through plain callables; the Qt
layer (:mod:`app.workers`) adapts them to signals.

Design notes
------------
* Downloads are performed **one video at a time**. This gives exact per-video
  status tracking (completed / skipped / failed / cancelled), correct playlist
  selection handling, and avoids the classic "downloaded the whole playlist"
  bug. A playlist is never handed to yt-dlp as a single job unless the caller
  explicitly asks for it.
* ``ignoreerrors`` is left **False**. Failures raise and are recorded
  individually instead of being silently swallowed.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .format_service import (
    build_postprocessors,
    format_selector,
    is_audio_quality,
)
from .utils import (
    KIND_INVALID,
    KIND_PLAYLIST,
    KIND_VIDEO,
    KIND_VIDEO_IN_PLAYLIST,
    FFmpegStatus,
    classify_url,
    detect_ffmpeg,
    ensure_dir,
    human_bytes,
    sanitize_filename,
)

try:  # yt-dlp is imported lazily-ish so unit tests can monkeypatch it
    import yt_dlp
    from yt_dlp.utils import DownloadError
    YTDLP_AVAILABLE = True
    YTDLP_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - exercised only without yt-dlp
    yt_dlp = None  # type: ignore
    DownloadError = Exception  # type: ignore
    YTDLP_AVAILABLE = False
    YTDLP_IMPORT_ERROR = str(exc)


STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class DownloadCancelled(Exception):
    """Raised internally to abort a yt-dlp download when the user cancels."""


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #

@dataclass
class PlaylistEntry:
    index: int
    id: str
    title: str
    url: str
    duration: Optional[float] = None
    available: bool = True
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "duration": self.duration,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass
class AnalysisResult:
    kind: str = KIND_INVALID
    url: str = ""
    title: str = ""
    id: str = ""
    uploader: str = ""
    duration: Optional[float] = None
    entry_count: int = 0
    entries: List[PlaylistEntry] = field(default_factory=list)
    formats: List[Dict[str, Any]] = field(default_factory=list)
    video_info: Dict[str, Any] = field(default_factory=dict)
    playlist_title: str = ""
    error: Optional[str] = None
    video_error: Optional[str] = None

    @property
    def is_playlist(self) -> bool:
        return self.kind in (KIND_PLAYLIST, KIND_VIDEO_IN_PLAYLIST)

    @property
    def has_video(self) -> bool:
        return self.kind in (KIND_VIDEO, KIND_VIDEO_IN_PLAYLIST)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "title": self.title,
            "id": self.id,
            "uploader": self.uploader,
            "duration": self.duration,
            "entry_count": self.entry_count,
            "entries": [e.to_dict() for e in self.entries],
            "formats": self.formats,
            "video_info": self.video_info,
            "playlist_title": self.playlist_title,
            "error": self.error,
            "video_error": self.video_error,
        }


@dataclass
class DownloadResult:
    url: str
    title: str = ""
    index: Optional[int] = None
    status: str = STATUS_FAILED
    filepath: Optional[str] = None
    filesize: Optional[int] = None
    error: Optional[str] = None
    elapsed: float = 0.0
    quality: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_COMPLETED, STATUS_SKIPPED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "index": self.index,
            "status": self.status,
            "filepath": self.filepath,
            "filesize": self.filesize,
            "error": self.error,
            "elapsed": self.elapsed,
            "quality": self.quality,
        }


@dataclass
class DownloadSummary:
    results: List[DownloadResult] = field(default_factory=list)
    started: float = 0.0
    ended: float = 0.0
    cancelled: bool = False
    output_dir: str = ""

    @property
    def completed(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == STATUS_COMPLETED]

    @property
    def skipped(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == STATUS_SKIPPED]

    @property
    def failed(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == STATUS_FAILED]

    @property
    def cancelled_results(self) -> List[DownloadResult]:
        return [r for r in self.results if r.status == STATUS_CANCELLED]

    @property
    def elapsed(self) -> float:
        return max(0.0, self.ended - self.started)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "completed": len(self.completed),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "cancelled": len(self.cancelled_results),
            "total": len(self.results),
            "elapsed": self.elapsed,
            "cancelled_job": self.cancelled,
            "output_dir": self.output_dir,
        }


# --------------------------------------------------------------------------- #
# yt-dlp logger adapter
# --------------------------------------------------------------------------- #

class _YdlLogger:
    """Forwards yt-dlp log lines to a callable, filtering noisy prefixes."""

    def __init__(self, sink: Callable[[str], None]):
        self._sink = sink

    def debug(self, msg: str) -> None:
        text = str(msg).strip()
        # yt-dlp emits debug for everything; keep only useful lines.
        if text.startswith("[debug]"):
            return
        if text:
            self._sink(text)

    def info(self, msg: str) -> None:
        text = str(msg).strip()
        if text:
            self._sink(text)

    def warning(self, msg: str) -> None:
        self._sink(f"WARNING: {str(msg).strip()}")

    def error(self, msg: str) -> None:
        self._sink(f"ERROR: {str(msg).strip()}")


class _TrackingYoutubeDL(yt_dlp.YoutubeDL if YTDLP_AVAILABLE else object):  # type: ignore[misc]
    """YoutubeDL subclass that lets us detect and skip existing files.

    yt-dlp does not expose a "skipped because file exists" hook, so we
    intercept ``process_info`` and short-circuit before the download begins.
    """

    def __init__(self, params, tracker: "DownloadTracker"):
        self._tracker = tracker
        super().__init__(params)

    def process_info(self, info_dict):  # type: ignore[override]
        tracker = self._tracker
        filename = None
        try:
            filename = self.prepare_filename(info_dict)
        except Exception:
            filename = None
        tracker.on_video_start(info_dict, filename)
        if (
            tracker.skip_existing
            and filename
            and os.path.exists(filename)
            and not os.path.exists(filename + ".part")
        ):
            tracker.on_video_skipped(info_dict, filename)
            return True
        return super().process_info(info_dict)


# --------------------------------------------------------------------------- #
# Progress tracker
# --------------------------------------------------------------------------- #

class DownloadTracker:
    """Collects progress/status for a single video download."""

    def __init__(
        self,
        index: Optional[int],
        title_hint: str,
        url: str,
        quality: str,
        skip_existing: bool,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        postproc_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.index = index
        self.title = title_hint
        self.url = url
        self.quality = quality
        self.skip_existing = skip_existing
        self.progress_cb = progress_cb
        self.postproc_cb = postproc_cb
        self.log_cb = log_cb
        self.cancel_check = cancel_check

        self.downloaded = False
        self.skipped = False
        self.started_at = time.time()
        self.final_filepath: Optional[str] = None
        self.candidates: List[str] = []
        self.error: Optional[str] = None

    # -- logging ----------------------------------------------------------- #
    def log(self, msg: str) -> None:
        if self.log_cb:
            self.log_cb(msg)

    def reset(self) -> None:
        """Reset per-attempt state so the tracker can be reused on retry."""
        self.downloaded = False
        self.skipped = False
        self.final_filepath = None
        self.candidates = []
        self.error = None
        self.started_at = time.time()

    # -- yt-dlp callbacks -------------------------------------------------- #
    def on_video_start(self, info: Dict[str, Any], filename: Optional[str]) -> None:
        self.title = info.get("title") or self.title
        if filename:
            self.candidates.append(filename)
        self.log(f"Downloading [{self.index if self.index is not None else '-'}]: {self.title}")

    def on_video_skipped(self, info: Dict[str, Any], filename: str) -> None:
        self.skipped = True
        self.final_filepath = filename
        self.log(f"Already downloaded, skipping: {filename}")

    def progress_hook(self, d: Dict[str, Any]) -> None:
        if self.cancel_check and self.cancel_check():
            raise DownloadCancelled("Cancelled by user")

        status = d.get("status")
        info = d.get("info_dict") or {}
        if info.get("title"):
            self.title = info["title"]
        fname = d.get("filename")
        if fname and fname not in self.candidates:
            self.candidates.append(fname)

        if status == "downloading":
            self.downloaded = True
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            payload = {
                "index": self.index,
                "title": self.title,
                "status": "downloading",
                "downloaded_bytes": d.get("downloaded_bytes") or 0,
                "total_bytes": total,
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "percent": (d.get("downloaded_bytes") or 0) / total if total else None,
                "fragment_index": d.get("fragment_index"),
                "fragment_count": d.get("fragment_count"),
                "filename": fname,
            }
            if self.progress_cb:
                self.progress_cb(payload)
        elif status == "finished":
            payload = {
                "index": self.index,
                "title": self.title,
                "status": "finished",
                "downloaded_bytes": d.get("downloaded_bytes") or 0,
                "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
                "percent": 1.0,
                "filename": fname,
            }
            if self.progress_cb:
                self.progress_cb(payload)

    def postprocessor_hook(self, d: Dict[str, Any]) -> None:
        if self.cancel_check and self.cancel_check():
            raise DownloadCancelled("Cancelled by user")
        info = d.get("info_dict") or {}
        pp = d.get("postprocessor", "")
        status = d.get("status", "")
        if pp:
            self.log(f"[{status}] postprocessor: {pp}")
        if self.postproc_cb:
            self.postproc_cb({
                "index": self.index,
                "title": self.title,
                "postprocessor": pp,
                "status": status,
            })
        # Merged output path appears here.
        fp = info.get("filepath") or d.get("filepath")
        if fp and os.path.exists(fp):
            self.final_filepath = fp

    # -- result ------------------------------------------------------------ #
    def resolve_filepath(self, info: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if self.final_filepath and os.path.exists(self.final_filepath):
            return self.final_filepath
        info = info or {}
        for rd in info.get("requested_downloads") or []:
            fp = rd.get("filepath")
            if fp and os.path.exists(fp):
                return fp
        # Merge may have changed the extension; prefer mp4/mkv results.
        existing = [c for c in self.candidates if os.path.exists(c)]
        media = [c for c in existing if Path(c).suffix.lower()
                 in (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".ogg", ".wav")]
        pool = media or existing
        if pool:
            return max(pool, key=lambda p: os.path.getsize(p))
        return None

    def build_result(self, status: str, error: Optional[str] = None,
                     info: Optional[Dict[str, Any]] = None) -> DownloadResult:
        fp = self.resolve_filepath(info)
        size = os.path.getsize(fp) if fp and os.path.exists(fp) else None
        return DownloadResult(
            url=self.url,
            title=self.title,
            index=self.index,
            status=status,
            filepath=fp,
            filesize=size,
            error=error,
            elapsed=time.time() - self.started_at,
            quality=self.quality,
        )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class DownloadService:
    """High level wrapper around yt-dlp used by the GUI workers."""

    def __init__(
        self,
        ffmpeg_status: Optional[FFmpegStatus] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ):
        self.ffmpeg = ffmpeg_status or detect_ffmpeg()
        self._log_cb = log_cb

    # -- logging ----------------------------------------------------------- #
    def _log(self, msg: str) -> None:
        if self._log_cb:
            self._log_cb(msg)

    def _ydl_logger(self) -> _YdlLogger:
        return _YdlLogger(self._log)

    @property
    def ffmpeg_available(self) -> bool:
        return self.ffmpeg.available

    # -- option builders --------------------------------------------------- #
    def base_options(
        self,
        *,
        skip_download: bool = False,
        extract_flat: Any = False,
        noplaylist: bool = True,
    ) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": noplaylist,
            "skip_download": skip_download,
            "extract_flat": extract_flat,
            "ignoreerrors": False,
            "nocheckcertificate": False,  # keep TLS verification enabled
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 5,
            "logger": self._ydl_logger(),
        }
        if self.ffmpeg.ffmpeg:
            opts["ffmpeg_location"] = str(Path(self.ffmpeg.ffmpeg).parent)
        return opts

    def _download_options(
        self,
        *,
        outtmpl: str,
        quality: str,
        skip_existing: bool,
        retries: int,
        concurrent_fragments: int,
        embed_metadata: bool,
        embed_thumbnail: bool,
        tracker: DownloadTracker,
    ) -> Dict[str, Any]:
        opts = self.base_options(skip_download=False, noplaylist=True)
        opts.update({
            "outtmpl": outtmpl,
            "format": format_selector(quality, self.ffmpeg_available),
            "overwrites": not skip_existing,
            "continuedl": True,          # resume partial downloads
            "retries": max(1, int(retries)),
            "fragment_retries": max(1, int(retries)),
            "concurrent_fragment_downloads": max(1, int(concurrent_fragments)),
            "postprocessors": build_postprocessors(
                quality,
                ffmpeg_available=self.ffmpeg_available,
                embed_metadata=embed_metadata,
                embed_thumbnail=embed_thumbnail,
            ),
            "progress_hooks": [tracker.progress_hook],
            "postprocessor_hooks": [tracker.postprocessor_hook],
            "logger": self._ydl_logger(),
        })
        if self.ffmpeg_available and not is_audio_quality(quality):
            opts["merge_output_format"] = "mp4"
        return opts

    # -- metadata ---------------------------------------------------------- #
    def _extract_with_fallback(
        self,
        url: str,
        opts_factory: Callable[[Optional[List[str]]], Dict[str, Any]],
        download: bool = False,
    ) -> Dict[str, Any]:
        """Extract info, retrying with the Android client if needed.

        YouTube sometimes returns UNPLAYABLE / "Requested format is not
        available" for the default player clients (notably for some
        made-for-kids or region-restricted videos), and recent yt-dlp builds
        require a JavaScript runtime for the web client. The Android client
        frequently still works, so we transparently retry with it.
        """
        try:
            with yt_dlp.YoutubeDL(opts_factory(None)) as ydl:
                return ydl.extract_info(url, download=download)
        except DownloadError as exc:
            if not _needs_client_fallback(exc):
                raise
            self._log(
                "Default YouTube client failed ("
                + str(exc).splitlines()[0][:100]
                + "). Retrying with the Android client…"
            )
            with yt_dlp.YoutubeDL(opts_factory(["android"])) as ydl:
                return ydl.extract_info(url, download=download)

    def extract_video_info(self, url: str) -> Dict[str, Any]:
        def factory(clients: Optional[List[str]]) -> Dict[str, Any]:
            opts = self.base_options(skip_download=True, noplaylist=True)
            if clients:
                opts.setdefault("extractor_args", {})["youtube"] = {"player_client": clients}
            return opts

        return self._extract_with_fallback(url, factory, download=False)

    def extract_playlist_info(self, url: str, limit: Optional[int] = None) -> Dict[str, Any]:
        def factory(clients: Optional[List[str]]) -> Dict[str, Any]:
            opts = self.base_options(
                skip_download=True, extract_flat="in_playlist", noplaylist=False
            )
            if limit:
                opts["playlistend"] = int(limit)
            if clients:
                opts.setdefault("extractor_args", {})["youtube"] = {"player_client": clients}
            return opts

        return self._extract_with_fallback(url, factory, download=False)

    def get_formats(self, url: str) -> List[Dict[str, Any]]:
        info = self.extract_video_info(url)
        return info.get("formats") or []

    def analyze(self, url: str, playlist_limit: Optional[int] = None) -> AnalysisResult:
        """Extract metadata for a video/playlist without downloading."""
        if not YTDLP_AVAILABLE:
            return AnalysisResult(
                kind=KIND_INVALID, url=url,
                error=f"yt-dlp is not available: {YTDLP_IMPORT_ERROR}",
            )

        kind = classify_url(url)
        result = AnalysisResult(kind=kind, url=url)
        if kind == KIND_INVALID:
            result.error = "Not a valid YouTube video or playlist URL."
            return result

        # Video part ---------------------------------------------------- #
        if kind in (KIND_VIDEO, KIND_VIDEO_IN_PLAYLIST):
            try:
                info = self.extract_video_info(url)
                result.video_info = info
                result.title = info.get("title") or ""
                result.id = info.get("id") or ""
                result.uploader = info.get("uploader") or info.get("channel") or ""
                result.duration = info.get("duration")
                result.formats = info.get("formats") or []
            except Exception as exc:  # noqa: BLE001
                msg = _friendly_error(exc)
                result.video_error = msg
                if kind == KIND_VIDEO:
                    result.error = msg
                    return result

        # Playlist part ------------------------------------------------- #
        if kind in (KIND_PLAYLIST, KIND_VIDEO_IN_PLAYLIST):
            try:
                pinfo = self.extract_playlist_info(url, playlist_limit)
                entries = self._parse_entries(pinfo)
                result.entries = entries
                result.entry_count = len(entries)
                result.playlist_title = pinfo.get("title") or ""
                if not result.title:
                    result.title = result.playlist_title
                if result.error and not entries:
                    # Both parts failed for a video-in-playlist URL.
                    pass
                elif entries:
                    result.error = None
            except Exception as exc:  # noqa: BLE001
                msg = _friendly_error(exc)
                result.error = (result.error + " | " + msg) if result.error else msg
        return result

    @staticmethod
    def _parse_entries(pinfo: Dict[str, Any]) -> List[PlaylistEntry]:
        entries: List[PlaylistEntry] = []
        raw = pinfo.get("entries") or []
        for i, item in enumerate(raw, start=1):
            if item is None:
                entries.append(PlaylistEntry(
                    index=i, id="", title="(unavailable)", url="",
                    available=False, reason="Entry could not be retrieved",
                ))
                continue
            title = item.get("title") or f"Entry {i}"
            vid = item.get("id") or ""
            url = item.get("url") or item.get("webpage_url") or ""
            if vid and (not url or not url.startswith("http")):
                url = f"https://www.youtube.com/watch?v={vid}"
            unavailable = _looks_unavailable(title, item)
            entries.append(PlaylistEntry(
                index=i,
                id=vid,
                title=title,
                url=url,
                duration=item.get("duration"),
                available=not unavailable,
                reason=title if unavailable else "",
            ))
        return entries

    # -- download ---------------------------------------------------------- #
    def download_video(
        self,
        *,
        url: str,
        outtmpl: str,
        quality: str,
        skip_existing: bool = True,
        retries: int = 3,
        concurrent_fragments: int = 4,
        embed_metadata: bool = True,
        embed_thumbnail: bool = False,
        index: Optional[int] = None,
        title_hint: str = "",
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        postproc_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> DownloadResult:
        """Download a single video and return a :class:`DownloadResult`."""
        if not YTDLP_AVAILABLE:
            return DownloadResult(
                url=url, index=index, title=title_hint, quality=quality,
                status=STATUS_FAILED,
                error=f"yt-dlp is not available: {YTDLP_IMPORT_ERROR}",
            )

        tracker = DownloadTracker(
            index=index, title_hint=title_hint, url=url, quality=quality,
            skip_existing=skip_existing, progress_cb=progress_cb,
            postproc_cb=postproc_cb, log_cb=self._log_cb,
            cancel_check=cancel_check,
        )
        opts = self._download_options(
            outtmpl=outtmpl, quality=quality, skip_existing=skip_existing,
            retries=retries, concurrent_fragments=concurrent_fragments,
            embed_metadata=embed_metadata, embed_thumbnail=embed_thumbnail,
            tracker=tracker,
        )

        info: Optional[Dict[str, Any]] = None
        last_error: Optional[Exception] = None
        for attempt, clients in enumerate((None, ["android"])):
            if attempt:
                tracker.reset()
                self._log("Retrying download with the Android client…")
                opts = self._download_options(
                    outtmpl=outtmpl, quality=quality, skip_existing=skip_existing,
                    retries=retries, concurrent_fragments=concurrent_fragments,
                    embed_metadata=embed_metadata, embed_thumbnail=embed_thumbnail,
                    tracker=tracker,
                )
                opts.setdefault("extractor_args", {})["youtube"] = {"player_client": clients}
            try:
                with _TrackingYoutubeDL(opts, tracker) as ydl:
                    info = ydl.extract_info(url, download=True)
                last_error = None
                break
            except DownloadCancelled as exc:
                self._log("Download cancelled by user.")
                return tracker.build_result(STATUS_CANCELLED, str(exc))
            except DownloadError as exc:
                last_error = exc
                if clients is None and _needs_client_fallback(exc):
                    continue
                msg = _friendly_error(exc)
                self._log(f"Download failed: {msg}")
                return tracker.build_result(STATUS_FAILED, msg)
            except Exception as exc:  # noqa: BLE001
                msg = _friendly_error(exc)
                self._log(f"Unexpected download error: {msg}")
                return tracker.build_result(STATUS_FAILED, msg)

        if info is None and last_error is not None:
            msg = _friendly_error(last_error)
            self._log(f"Download failed: {msg}")
            return tracker.build_result(STATUS_FAILED, msg)

        if tracker.skipped:
            return tracker.build_result(STATUS_SKIPPED, info=info)
        if not tracker.downloaded:
            # Nothing was downloaded and it was not flagged as skipped.
            return tracker.build_result(
                STATUS_FAILED, "Download produced no media (unknown yt-dlp state).",
                info=info,
            )
        result = tracker.build_result(STATUS_COMPLETED, info=info)
        if result.filepath is None:
            result.status = STATUS_FAILED
            result.error = "Download finished but the output file was not found."
        elif result.filesize == 0:
            result.status = STATUS_FAILED
            result.error = "Downloaded file is empty (0 bytes)."
        return result

    def download_many(
        self,
        *,
        tasks: List[Dict[str, Any]],
        output_dir: str,
        quality: str,
        make_playlist_folder: bool = False,
        playlist_title: str = "",
        skip_existing: bool = True,
        retries: int = 3,
        concurrent_fragments: int = 4,
        embed_metadata: bool = True,
        embed_thumbnail: bool = False,
        number_files: bool = False,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        postproc_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        video_done_cb: Optional[Callable[[DownloadResult], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> DownloadSummary:
        """Download a list of ``tasks`` sequentially, tracking each result.

        Each task is ``{"url": str, "title": str, "index": int|None}``.
        """
        target_dir = Path(output_dir).expanduser()
        if make_playlist_folder and playlist_title:
            target_dir = target_dir / sanitize_filename(playlist_title)
        ensure_dir(target_dir)

        summary = DownloadSummary(started=time.time(), output_dir=str(target_dir))
        total = len(tasks)
        for pos, task in enumerate(tasks):
            if cancel_check and cancel_check():
                summary.cancelled = True
                # Record the remaining tasks as cancelled for an accurate report.
                for rest in tasks[pos:]:
                    summary.results.append(DownloadResult(
                        url=rest.get("url", ""), title=rest.get("title", ""),
                        index=rest.get("index"), status=STATUS_CANCELLED,
                        error="Cancelled before start", quality=quality,
                    ))
                    if video_done_cb:
                        video_done_cb(summary.results[-1])
                break

            index = task.get("index")
            if number_files and index is not None:
                template = f"{int(index):03d} - %(title)s.%(ext)s"
            else:
                template = "%(title)s.%(ext)s"
            outtmpl = str(target_dir / template)

            if total > 1:
                self._log(f"--- Video {pos + 1}/{total} ---")

            result = self.download_video(
                url=task["url"],
                outtmpl=outtmpl,
                quality=quality,
                skip_existing=skip_existing,
                retries=retries,
                concurrent_fragments=concurrent_fragments,
                embed_metadata=embed_metadata,
                embed_thumbnail=embed_thumbnail,
                index=index,
                title_hint=task.get("title", ""),
                progress_cb=progress_cb,
                postproc_cb=postproc_cb,
                cancel_check=cancel_check,
            )
            summary.results.append(result)
            if video_done_cb:
                video_done_cb(result)
            self._log(_result_line(result))

        summary.ended = time.time()
        return summary


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_UNAVAILABLE_MARKERS = (
    "private video", "deleted video", "video unavailable",
    "this video is not available", "[removed]", "members-only",
    "sign in to confirm your age", "premieres in",
)


_CLIENT_FALLBACK_MARKERS = (
    "not available",
    "unplayable",
    "requested format is not available",
    "no supported javascript",
    "page needs to be reloaded",
    "unable to extract",
    "sign in to confirm you're not a bot",
)


def _needs_client_fallback(exc: Exception) -> bool:
    """Whether a different YouTube player client is worth trying."""
    low = str(exc).lower()
    return any(marker in low for marker in _CLIENT_FALLBACK_MARKERS)


def _looks_unavailable(title: str, item: Dict[str, Any]) -> bool:
    if item.get("availability") in ("private", "premium_only", "subscriber_only"):
        return True
    lowered = (title or "").strip().lower()
    return any(marker in lowered for marker in _UNAVAILABLE_MARKERS)


def _result_line(result: DownloadResult) -> str:
    label = f"[{result.index if result.index is not None else '-'}]"
    if result.status == STATUS_COMPLETED:
        return f"{label} DONE  {result.title}  ({human_bytes(result.filesize)})"
    if result.status == STATUS_SKIPPED:
        return f"{label} SKIP  {result.title} (already downloaded)"
    if result.status == STATUS_CANCELLED:
        return f"{label} STOP  {result.title}"
    return f"{label} FAIL  {result.title} — {result.error}"


def _friendly_error(exc: Exception) -> str:
    """Turn a yt-dlp exception into an actionable, secret-free message."""
    msg = str(exc)
    low = msg.lower()

    if "not a valid url" in low or "unsupported url" in low:
        return "The URL is not supported by yt-dlp."
    if "private video" in low:
        return "This video is private and cannot be downloaded."
    if "sign in to confirm your age" in low or "age" in low and "restricted" in low:
        return "This video is age-restricted and requires authentication."
    if "video unavailable" in low or "not available" in low:
        return "The video is unavailable (removed, private, or region-blocked)."
    if "members-only" in low or "join this channel" in low:
        return "This video is members-only and requires a membership."
    if "this playlist is private" in low:
        return "The playlist is private and cannot be accessed."
    if "http error 429" in low or "too many requests" in low:
        return "YouTube is rate limiting requests (HTTP 429). Wait and retry."
    if "http error 403" in low:
        return "Access denied (HTTP 403). The video may be region-blocked."
    if "unable to download webpage" in low or "getaddrinfo" in low or "timed out" in low \
            or "network" in low or "connection" in low:
        return "Network error while contacting YouTube. Check your connection."
    if "ffmpeg" in low:
        return f"FFmpeg problem: {msg}"
    if "no space left" in low or "disk full" in low:
        return "The destination drive is out of space."
    if "permission denied" in low or "access is denied" in low:
        return "Permission denied writing to the output folder."
    # Fall back to the raw message, truncated.
    return msg[:400] if msg else exc.__class__.__name__
