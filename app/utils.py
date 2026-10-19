"""Utility helpers: URL validation, filename sanitisation, human formatting,
path helpers and FFmpeg/FFprobe detection.

This module is intentionally free of any Qt import so it can be unit tested in
isolation and reused from worker threads.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

# The supplied integration-test playlist (kept in one place for tests/scripts).
DEFAULT_TEST_URL = (
    "https://www.youtube.com/watch?v=CUJcKAF5wG8"
    "&list=PLV-cxl3VSwWE_ocGSxzOhIrLp4X1cDcxk"
)

# URL kinds returned by :func:`classify_url`.
KIND_VIDEO = "video"
KIND_PLAYLIST = "playlist"
KIND_VIDEO_IN_PLAYLIST = "video_in_playlist"
KIND_INVALID = "invalid"


def normalize_url(raw: str) -> str:
    """Strip surrounding whitespace and a single pair of quotes."""
    if raw is None:
        return ""
    url = raw.strip()
    if len(url) >= 2 and url[0] == url[-1] and url[0] in ("'", '"'):
        url = url[1:-1].strip()
    return url


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def is_youtube_url(url: str) -> bool:
    """Return True when *url* points at a recognised YouTube host."""
    url = normalize_url(url)
    if not url:
        return False
    host = _host(url)
    if not host:
        return False
    # Allow sub-domains such as ``www.`` or ``m.``.
    if host in _YOUTUBE_HOSTS:
        return True
    base = host.split(":", 1)[0]
    return any(base == h or base.endswith("." + h) for h in _YOUTUBE_HOSTS)


def extract_video_id(url: str) -> Optional[str]:
    """Return the ``v`` video id from a YouTube URL, or None."""
    url = normalize_url(url)
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    qs = parse_qs(parsed.query)
    vid = (qs.get("v") or [None])[0]
    return vid or None


def extract_playlist_id(url: str) -> Optional[str]:
    """Return the ``list`` playlist id from a YouTube URL, or None."""
    url = normalize_url(url)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    plid = (qs.get("list") or [None])[0]
    return plid or None


def classify_url(url: str) -> str:
    """Classify a URL as video / playlist / video_in_playlist / invalid."""
    url = normalize_url(url)
    if not url or not is_youtube_url(url):
        return KIND_INVALID
    parsed = urlparse(url)
    path = parsed.path.lower()
    has_video = bool(extract_video_id(url))
    has_list = bool(extract_playlist_id(url))

    if has_video and has_list:
        return KIND_VIDEO_IN_PLAYLIST
    if has_list:
        return KIND_PLAYLIST
    if has_video:
        return KIND_VIDEO
    # ``/playlist?list=`` handled above; accept bare /watch with no id as invalid
    if path.startswith("/playlist"):
        return KIND_PLAYLIST if has_list else KIND_INVALID
    return KIND_INVALID


def validate_url(url: str) -> tuple[bool, Optional[str]]:
    """Validate a user supplied URL.

    Returns ``(is_valid, error_message)``. The message is user friendly and
    safe to display directly in the GUI.
    """
    url = normalize_url(url)
    if not url:
        return False, "Please enter a YouTube video or playlist URL."
    if "://" not in url:
        return False, "The URL is missing a scheme (it should start with https://)."
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "The URL could not be parsed."
    if parsed.scheme not in ("http", "https"):
        return False, "Only http(s) URLs are supported."
    if not is_youtube_url(url):
        return False, (
            "This does not look like a YouTube URL. "
            "Supported hosts: youtube.com and youtu.be."
        )
    if classify_url(url) == KIND_INVALID:
        return False, (
            "The URL is a YouTube link but does not contain a video id (v=) "
            "or a playlist id (list=)."
        )
    return True, None


# --------------------------------------------------------------------------- #
# Filename / path handling
# --------------------------------------------------------------------------- #

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM = 150


def sanitize_filename(name: str, replacement: str = "_", max_length: int = _MAX_STEM) -> str:
    """Return a Windows-safe filename stem.

    * Removes characters that are illegal on Windows.
    * Collapses whitespace.
    * Truncates overly long names while keeping an extension-free stem.
    * Avoids reserved device names (CON, NUL, COM1 ...) and trailing dots.
    """
    if name is None:
        return "untitled"
    # Replace illegal characters and strip control characters.
    cleaned = _ILLEGAL_CHARS.sub(replacement, str(name))
    # Collapse runs of whitespace / replacement into a single space.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(re.escape(replacement) + r"{2,}", replacement, cleaned)
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = "untitled"
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "untitled"


def unique_path(path: Path) -> Path:
    """Return *path* or ``name (1).ext`` style variant if it already exists."""
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def ensure_dir(path: str | Path) -> Path:
    """Create *path* (recursively) and return it as a ``Path``."""
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_writable_dir(path: str | Path) -> bool:
    """Best-effort check that *path* is an existing, writable directory."""
    try:
        p = Path(path).expanduser()
        if not p.is_dir():
            return False
        probe = p / ".write_test.tmp"
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def default_output_dir() -> str:
    """Default download directory: ``%USERPROFILE%\\Downloads\\YouTube``."""
    home = Path(os.path.expanduser("~"))
    downloads = home / "Downloads"
    if not downloads.is_dir():
        downloads = home
    return str(downloads / "YouTube")


# --------------------------------------------------------------------------- #
# Human readable formatting
# --------------------------------------------------------------------------- #

def human_bytes(num: Optional[float]) -> str:
    """Format a byte count as e.g. ``1.5 GiB``. Returns '—' for unknown."""
    if num is None:
        return "—"
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "—"
    if num < 0:
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if num < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return "—"


def human_duration(seconds: Optional[float]) -> str:
    """Format seconds as ``H:MM:SS`` or ``M:SS``. Returns '—' for unknown."""
    if seconds is None:
        return "—"
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if seconds < 0:
        return "—"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    return human_duration(seconds)


# --------------------------------------------------------------------------- #
# FFmpeg / FFprobe detection
# --------------------------------------------------------------------------- #

@dataclass
class FFmpegStatus:
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None
    ffmpeg_version: Optional[str] = None
    ffprobe_version: Optional[str] = None
    source: str = "not found"
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    @property
    def partial(self) -> bool:
        return bool(self.ffmpeg) ^ bool(self.ffprobe)

    def describe(self) -> str:
        if self.available:
            return (
                f"FFmpeg found ({self.source}): {self.ffmpeg}\n"
                f"FFprobe found: {self.ffprobe}"
            )
        if self.partial:
            missing = "FFprobe" if self.ffmpeg else "FFmpeg"
            return f"Partial: {missing} is missing. Merging/verification limited."
        return "FFmpeg and FFprobe were not found."


def _run_version(exe: str, timeout: int = 10) -> Optional[str]:
    try:
        out = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout or "").splitlines()
    return first[0].strip() if first else None


def find_executable(name: str, extra_dirs: Optional[Iterable[str]] = None) -> Optional[str]:
    """Locate an executable on PATH or in the provided extra directories."""
    found = shutil.which(name)
    if found:
        return found
    candidates: list[str] = []
    if extra_dirs:
        exe = f"{name}.exe" if sys.platform == "win32" else name
        for d in extra_dirs:
            if not d:
                continue
            candidates.append(str(Path(d) / name))
            candidates.append(str(Path(d) / exe))
            candidates.append(str(Path(d) / "bin" / exe))
    for cand in candidates:
        if Path(cand).is_file():
            return cand
    return None


def detect_ffmpeg(ffmpeg_location: Optional[str] = None) -> FFmpegStatus:
    """Detect FFmpeg and FFprobe.

    *ffmpeg_location* may be a directory containing the binaries, a full path
    to ``ffmpeg``, or None to search PATH only.
    """
    status = FFmpegStatus()
    # Always look next to the running application (covers a PyInstaller build
    # that ships ffmpeg.exe/ffprobe.exe alongside the executable).
    extra_dirs: list[str] = []
    try:
        extra_dirs.append(str(Path(sys.executable).resolve().parent))
    except OSError:
        pass
    if getattr(sys, "frozen", False):  # pragma: no cover - only in bundled builds
        extra_dirs.append(str(Path(getattr(sys, "_MEIPASS", ".")).resolve()))
    # Common Windows install locations that may not be on PATH yet (e.g. a
    # fresh winget install before the shell has been restarted).
    local = os.environ.get("LOCALAPPDATA")
    if local:
        links = Path(local) / "Microsoft" / "WinGet" / "Links"
        if links.is_dir():
            extra_dirs.append(str(links))
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            try:
                for exe in packages.glob("*/**/ffmpeg.exe"):
                    extra_dirs.append(str(exe.parent))
            except OSError:
                pass
    explicit_ffmpeg: Optional[str] = None

    if ffmpeg_location:
        loc = Path(ffmpeg_location).expanduser()
        if loc.is_file():
            explicit_ffmpeg = str(loc)
            extra_dirs.append(str(loc.parent))
            status.source = "configured path"
        elif loc.is_dir():
            extra_dirs.append(str(loc))
            status.source = "configured directory"
        else:
            status.notes.append(f"Configured FFmpeg location does not exist: {loc}")

    status.ffmpeg = explicit_ffmpeg or find_executable("ffmpeg", extra_dirs)
    status.ffprobe = find_executable("ffprobe", extra_dirs)

    if status.ffmpeg and status.source == "not found":
        status.source = _describe_ffmpeg_source(status.ffmpeg)
    if status.ffmpeg:
        status.ffmpeg_version = _run_version(status.ffmpeg)
    if status.ffprobe:
        status.ffprobe_version = _run_version(status.ffprobe)
    return status


def _describe_ffmpeg_source(path: str) -> str:
    """Best-effort label for where the FFmpeg binary came from."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return "system PATH"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            resolved.relative_to(Path(meipass).resolve())
            return "bundled with app"
        except (ValueError, OSError):
            pass
    try:
        if resolved.parent == Path(sys.executable).resolve().parent:
            return "bundled with app"
    except OSError:
        pass
    return "system PATH"


def probe_media(path: str | Path, ffprobe_path: Optional[str] = None, timeout: int = 60) -> dict:
    """Run FFprobe on *path* and return a small dict of media properties.

    Returns ``{"ok": bool, "error": str|None, ...}``. Never raises.
    """
    result: dict = {"ok": False, "error": None}
    p = Path(path)
    if not p.is_file():
        result["error"] = f"File does not exist: {p}"
        return result
    if p.stat().st_size == 0:
        result["error"] = "File is empty (0 bytes)."
        return result
    exe = ffprobe_path or find_executable("ffprobe")
    if not exe:
        result["error"] = "FFprobe not available; cannot verify media."
        return result
    cmd = [
        exe, "-v", "error",
        "-show_entries", "format=duration,size,format_name:stream=codec_type,codec_name,width,height",
        "-of", "json", str(p),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = f"FFprobe failed to run: {exc}"
        return result
    if proc.returncode != 0:
        result["error"] = f"FFprobe returned {proc.returncode}: {proc.stderr.strip()[:300]}"
        return result
    import json
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        result["error"] = f"FFprobe output was not valid JSON: {exc}"
        return result
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    result["ok"] = True
    result["format_name"] = fmt.get("format_name")
    result["duration"] = float(fmt["duration"]) if fmt.get("duration") else None
    result["size"] = int(fmt["size"]) if fmt.get("size") else p.stat().st_size
    result["streams"] = streams
    result["video"] = next((s for s in streams if s.get("codec_type") == "video"), None)
    result["audio"] = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return result
