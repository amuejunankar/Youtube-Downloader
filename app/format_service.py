"""Format discovery and selection logic.

All functions here are pure (no Qt, no network) and therefore fully unit
testable. The GUI and the downloader both consume these helpers so that the
quality shown in the interface matches the quality actually downloaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Quality keys ------------------------------------------------------------- #
QUALITY_BEST = "best"
QUALITY_AUDIO = "audio"
QUALITY_AUDIO_MP3 = "audio_mp3"

STANDARD_HEIGHTS: List[int] = [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]

# Human friendly labels for common heights.
_HEIGHT_LABELS = {
    4320: "4320p (8K)",
    2160: "2160p (4K)",
    1440: "1440p (2K)",
    1080: "1080p (Full HD)",
    720: "720p (HD)",
    480: "480p",
    360: "360p",
    240: "240p",
    144: "144p",
}

MAX_PLAYLIST_RESOLUTION_NOTE = (
    "For playlists this is a maximum resolution: each video is downloaded at "
    "the best available format at or below this height."
)


def height_label(height: int) -> str:
    return _HEIGHT_LABELS.get(height, f"{height}p")


# --------------------------------------------------------------------------- #
# Format introspection
# --------------------------------------------------------------------------- #

def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_size(fmt: Dict[str, Any]) -> Optional[int]:
    """Best known size (exact preferred over approximation), or None."""
    for key in ("filesize", "filesize_approx"):
        val = fmt.get(key)
        if val:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def is_video(fmt: Dict[str, Any]) -> bool:
    return fmt.get("vcodec") not in (None, "none")


def is_audio(fmt: Dict[str, Any]) -> bool:
    return fmt.get("acodec") not in (None, "none")


def has_both(fmt: Dict[str, Any]) -> bool:
    return is_video(fmt) and is_audio(fmt)


def summarize_format(fmt: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a compact, display-ready summary of a yt-dlp format dict."""
    height = fmt.get("height")
    width = fmt.get("width")
    if height:
        resolution = f"{width}x{height}" if width else f"{height}p"
    elif is_audio(fmt) and not is_video(fmt):
        resolution = fmt.get("format_note") or "audio"
    else:
        resolution = fmt.get("format_note") or "?"
    size = format_size(fmt)
    return {
        "format_id": str(fmt.get("format_id", "")),
        "ext": fmt.get("ext", ""),
        "resolution": resolution,
        "height": int(height) if height else None,
        "width": int(width) if width else None,
        "fps": int(fmt["fps"]) if fmt.get("fps") else None,
        "vcodec": fmt.get("vcodec") or None,
        "acodec": fmt.get("acodec") or None,
        "abr": _num(fmt.get("abr")),
        "tbr": _num(fmt.get("tbr")),
        "filesize": size,
        "note": fmt.get("format_note") or "",
        "has_video": is_video(fmt),
        "has_audio": is_audio(fmt),
        "combined": has_both(fmt),
    }


def list_summaries(formats: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if not formats:
        return []
    return [summarize_format(f) for f in formats if f]


def available_video_heights(formats: Optional[List[Dict[str, Any]]]) -> List[int]:
    """Distinct video heights, descending. Empty list if no video streams."""
    heights = set()
    for fmt in formats or []:
        if is_video(fmt) and fmt.get("height"):
            try:
                heights.add(int(fmt["height"]))
            except (TypeError, ValueError):
                continue
    return sorted(heights, reverse=True)


def has_combined_format(formats: Optional[List[Dict[str, Any]]]) -> bool:
    return any(has_both(f) for f in formats or [])


def available_quality_options(
    formats: Optional[List[Dict[str, Any]]],
    ffmpeg_available: bool = True,
) -> List[tuple[str, str]]:
    """Return ``[(key, label), ...]`` of qualities supported by *formats*.

    Only resolutions actually present in *formats* are offered. Audio options
    are always offered; MP3 conversion requires FFmpeg.
    """
    options: List[tuple[str, str]] = [(QUALITY_BEST, "Best available quality")]
    for height in available_video_heights(formats):
        options.append((str(height), height_label(height)))
    options.append((QUALITY_AUDIO, "Audio only (original container)"))
    if ffmpeg_available:
        options.append((QUALITY_AUDIO_MP3, "Audio only (MP3)"))
    return options


def default_quality_options(ffmpeg_available: bool = True) -> List[tuple[str, str]]:
    """Generic quality list for playlists where per-video formats differ."""
    options: List[tuple[str, str]] = [(QUALITY_BEST, "Best available quality")]
    for height in STANDARD_HEIGHTS:
        options.append((str(height), height_label(height)))
    options.append((QUALITY_AUDIO, "Audio only (original container)"))
    if ffmpeg_available:
        options.append((QUALITY_AUDIO_MP3, "Audio only (MP3)"))
    return options


# --------------------------------------------------------------------------- #
# Selection resolution
# --------------------------------------------------------------------------- #

def is_audio_quality(quality: str) -> bool:
    return quality in (QUALITY_AUDIO, QUALITY_AUDIO_MP3)


def target_height(quality: str) -> Optional[int]:
    """Return the maximum height encoded by *quality*, or None for best/audio."""
    if quality in (QUALITY_BEST, QUALITY_AUDIO, QUALITY_AUDIO_MP3):
        return None
    try:
        return int(quality)
    except (TypeError, ValueError):
        return None


def format_selector(quality: str, ffmpeg_available: bool = True) -> str:
    """Build the yt-dlp ``format`` string for a quality key.

    When FFmpeg is unavailable we must avoid separate streams that would need
    merging, so we prefer progressive (combined audio+video) formats.
    """
    if is_audio_quality(quality):
        return "bestaudio/best"

    height = target_height(quality)

    if ffmpeg_available:
        if height is None:
            return "bestvideo+bestaudio/best"
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )

    # No FFmpeg -> single-file formats only.
    if height is None:
        return "best[acodec!=none][vcodec!=none]/best"
    return (
        f"best[height<={height}][acodec!=none][vcodec!=none]/"
        f"best[height<={height}]/best"
    )


def _pick_best(formats, predicate):
    candidates = [f for f in formats if predicate(f)]
    if not candidates:
        return None

    def key(f):
        return (
            f.get("height") or 0,
            f.get("tbr") or 0,
            f.get("filesize") or f.get("filesize_approx") or 0,
        )

    return max(candidates, key=key)


@dataclass
class ResolvedFormat:
    """The format(s) that will actually be used for a download."""

    quality: str
    video: Optional[Dict[str, Any]] = None
    audio: Optional[Dict[str, Any]] = None
    fallback: bool = False
    note: str = ""
    needs_merge: bool = False

    def describe(self) -> str:
        if self.quality in (QUALITY_AUDIO, QUALITY_AUDIO_MP3):
            src = self.audio or {}
            return f"Audio only ({src.get('ext', '?')}, abr {src.get('abr') or '?'})"
        parts = []
        if self.video:
            parts.append(f"video {self.video.get('height') or '?'}p "
                         f"[{self.video.get('vcodec') or '?'}]")
        if self.audio:
            parts.append(f"audio [{self.audio.get('acodec') or '?'}]")
        text = " + ".join(parts) if parts else "unknown"
        if self.needs_merge:
            text += " (merged with FFmpeg)"
        if self.fallback:
            text += " — fallback applied"
        return text


def resolve_format(
    quality: str,
    formats: Optional[List[Dict[str, Any]]],
    ffmpeg_available: bool = True,
) -> ResolvedFormat:
    """Determine the concrete format(s) a quality choice maps to.

    Used by the GUI to display the *actual* selection and to warn when the
    requested quality is unavailable and a fallback is used.
    """
    formats = formats or []

    if is_audio_quality(quality):
        audio = _pick_best(formats, lambda f: is_audio(f) and not is_video(f))
        audio = audio or _pick_best(formats, is_audio)
        note = "" if audio else "No dedicated audio stream found; yt-dlp will choose."
        return ResolvedFormat(quality=quality, audio=audio, note=note)

    requested = target_height(quality)
    video_only = [f for f in formats if is_video(f) and not f.get("height") is None]

    if ffmpeg_available:
        video = None
        if requested is None:
            video = _pick_best(formats, is_video)
        else:
            video = _pick_best(
                formats, lambda f: is_video(f) and (f.get("height") or 0) <= requested
            )
        if video is None:
            # Fall back to any video stream at all.
            video = _pick_best(formats, is_video)
        audio = _pick_best(formats, lambda f: is_audio(f) and not is_video(f))
        fallback = bool(
            requested is not None
            and video is not None
            and (video.get("height") or 0) < requested
        )
        note = ""
        if fallback and video is not None:
            note = (
                f"{height_label(requested)} is not available; the best available "
                f"is {video.get('height')}p. Downloading at {video.get('height')}p."
            )
        elif video is None:
            note = "No video stream found; only audio may be available."
        needs_merge = bool(video and audio and not has_both(video))
        return ResolvedFormat(
            quality=quality, video=video, audio=audio,
            fallback=fallback, note=note, needs_merge=needs_merge,
        )

    # No FFmpeg: choose a combined (progressive) format.
    if requested is None:
        video = _pick_best(formats, has_both) or _pick_best(formats, is_video)
    else:
        video = _pick_best(
            formats, lambda f: has_both(f) and (f.get("height") or 0) <= requested
        )
        if video is None:
            video = _pick_best(formats, has_both) or _pick_best(formats, is_video)
    fallback = bool(
        requested is not None
        and video is not None
        and (video.get("height") or 0) < requested
    )
    note = ""
    if fallback and video is not None:
        note = (
            f"{height_label(requested)} is not available as a single file and "
            f"FFmpeg is missing; downloading {video.get('height')}p instead."
            if video.get("height") else
            "Requested resolution unavailable; using the best single-file format."
        )
    elif video is None:
        note = "No downloadable combined format found."
    return ResolvedFormat(
        quality=quality, video=video, fallback=fallback, note=note, needs_merge=False
    )


def build_postprocessors(
    quality: str,
    ffmpeg_available: bool = True,
    embed_metadata: bool = True,
    embed_thumbnail: bool = False,
    subtitle_langs: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return yt-dlp postprocessor configs for a quality choice.

    MP3 conversion and embedding require FFmpeg; when it is missing those
    postprocessors are skipped so the app never claims a conversion happened.
    """
    pps: List[Dict[str, Any]] = []
    if not ffmpeg_available:
        return pps

    if quality == QUALITY_AUDIO_MP3:
        pps.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })
    if embed_metadata:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True})
    if embed_thumbnail:
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    if subtitle_langs:
        pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    return pps
