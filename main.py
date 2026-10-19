"""YouTube Downloader - application entry point.

Run with::

    python main.py

Sets up persistent file logging (never logs cookies/secrets), then launches the
PySide6 GUI. Supports ``--version`` and ``--selftest`` (headless import check).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app import APP_NAME, __version__  # noqa: E402


def _log_dir() -> Path:
    if getattr(sys, "frozen", False):  # bundled .exe: keep logs outside the temp dir
        base = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "YouTubeDownloader"
        d = base / "logs"
    else:
        d = APP_ROOT / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        fallback = Path(os.path.expanduser("~")) / "YouTubeDownloader" / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure a rotating file log plus a console handler."""
    logger = logging.getLogger("youtube_downloader")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(module)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        fh = logging.handlers.RotatingFileHandler(
            _log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError as exc:  # pragma: no cover
        print(f"Warning: could not create log file: {exc}", file=sys.stderr)

    # Windows consoles often default to cp1252, which cannot encode video
    # titles containing non-Latin characters; never let logging crash the app.
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logger.info("=" * 70)
    logger.info("%s v%s starting (Python %s)", APP_NAME, __version__, sys.version.split()[0])
    return logger


def main(argv: list[str] | None = None) -> int:
    # A --windowed build has no console: stdout/stderr are None and any print()
    # would raise. Redirect them to a null sink so logging and diagnostics never
    # crash the app.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{__version__}")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--selftest", action="store_true",
                        help="import all modules and exit (headless smoke test)")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} v{__version__}")
        return 0

    logger = setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.selftest:
        from app.downloader import DownloadService  # noqa: F401
        from app.format_service import resolve_format  # noqa: F401
        from app.main_window import MainWindow  # noqa: F401
        from app.settings import Settings  # noqa: F401
        from app.utils import detect_ffmpeg
        from app.workers import AnalyzeWorker, DownloadWorker  # noqa: F401

        st = detect_ffmpeg()
        logger.info("Self-test imports OK. FFmpeg available=%s (%s)", st.available, st.source)
        logger.info("FFmpeg path=%s", st.ffmpeg)
        logger.info("FFmpeg version=%s", st.ffmpeg_version)
        logger.info("FFprobe version=%s", st.ffprobe_version)
        print(f"Self-test OK. FFmpeg available={st.available} ({st.source})")
        print(f"  ffmpeg : {st.ffmpeg}")
        print(f"  version: {st.ffmpeg_version}")
        return 0

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except ImportError as exc:
        logger.error("PySide6 is not installed: %s", exc)
        print("PySide6 is required. Install dependencies with: pip install -r requirements.txt",
              file=sys.stderr)
        return 2

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    from app.main_window import MainWindow
    from app.settings import Settings

    settings = Settings()
    window = MainWindow(settings=settings)
    window.show()
    logger.info("GUI launched.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
