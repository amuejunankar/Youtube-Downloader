"""Generate ``assets/app.ico`` from the in-app logo.

The logo is drawn by :meth:`app.main_window.MainWindow._make_logo_pixmap`, so
the icon shown in the taskbar/window and the icon embedded in the built
executable stay identical. Run this before a PyInstaller build:

    python scripts/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.main_window import MainWindow  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_bytes(size: int) -> bytes:
    pixmap = MainWindow._make_logo_pixmap(size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico(path: Path) -> None:
    """Write a multi-resolution .ico (each frame is an embedded PNG)."""
    images = [_png_bytes(s) for s in SIZES]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = b""
    payload = b""
    for size, img in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
        payload += img
    path.write_bytes(header + entries + payload)


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    out_dir = ROOT / "assets"
    out_dir.mkdir(exist_ok=True)
    ico = out_dir / "app.ico"
    build_ico(ico)
    print(f"Wrote {ico} ({ico.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
