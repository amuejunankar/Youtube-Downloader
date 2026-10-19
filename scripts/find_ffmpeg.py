"""Print the resolved FFmpeg / FFprobe paths, one per line.

Used by ``scripts/build_exe.bat`` so the real binaries (not WinGet symlinks in
``WinGet\\Links``) are bundled into the executable. Prints empty lines when a
binary cannot be found.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.utils import detect_ffmpeg  # noqa: E402


def _resolve(path: str | None) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def main() -> int:
    status = detect_ffmpeg()
    sys.stdout.write(_resolve(status.ffmpeg) + "\n")
    sys.stdout.write(_resolve(status.ffprobe) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
