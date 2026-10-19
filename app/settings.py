"""Persistent application settings.

Stored as JSON under ``%APPDATA%\\YouTubeDownloader\\settings.json`` (or the
platform equivalent). Deliberately Qt-free so it can be unit tested.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .utils import default_output_dir

DEFAULTS: Dict[str, Any] = {
    "output_dir": None,            # resolved lazily to default_output_dir()
    "quality": "best",             # quality choice key
    "download_mode": "video",      # video | playlist | selected
    "make_playlist_folder": True,
    "skip_existing": True,
    "retries": 3,
    "concurrent_fragments": 4,
    "ffmpeg_location": "",
    "embed_thumbnail": False,
    "embed_metadata": True,
    "write_subs": False,
    "audio_format": "mp3",
    "cookie_file": "",
    "window_geometry": None,
}


def config_dir() -> Path:
    """Return the per-user configuration directory for the application."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif os.sys.platform == "darwin":  # pragma: no cover - non-Windows
        base = str(Path.home() / "Library" / "Application Support")
    else:  # pragma: no cover - Linux
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "YouTubeDownloader"


class Settings:
    """A tiny JSON-backed settings store with sensible defaults."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else config_dir() / "settings.json"
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # -- persistence ------------------------------------------------------- #
    def load(self) -> None:
        try:
            if self._path.is_file():
                with open(self._path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self._data.update(loaded)
        except (OSError, ValueError):
            # Corrupt or unreadable settings should never crash the app.
            self._data = dict(DEFAULTS)
        if not self._data.get("output_dir"):
            self._data["output_dir"] = default_output_dir()

    def save(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
            return True
        except OSError:
            return False

    # -- accessors --------------------------------------------------------- #
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, mapping: Dict[str, Any]) -> None:
        self._data.update(mapping)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    @property
    def path(self) -> Path:
        return self._path


def load_settings(path: str | Path | None = None) -> Settings:
    """Convenience factory mirroring :class:`Settings`."""
    return Settings(path)
