"""PySide6 main window for the YouTube Downloader.

The window is deliberately minimal: one column, one primary action.

    [ link                                                     ] [Paste] [Download]
    [Video] [MP3]   Quality ▾                      More options
    Saved to  C:\\Users\\you\\Downloads\\YouTube   Change   Open

Pressing **Download** analyzes the link automatically (no separate Analyze
step) and then downloads it. Playlist links reveal a checklist; the same
Download button then downloads the ticked videos.

All network work happens in worker threads; every widget update here runs on
the GUI thread because it is triggered from Qt signals.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QLinearGradient,
    QPalette,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__, ytdlp_updater
from .downloader import (
    KIND_PLAYLIST,
    KIND_VIDEO,
    KIND_VIDEO_IN_PLAYLIST,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from .format_service import (
    QUALITY_AUDIO_MP3,
    QUALITY_BEST,
    available_quality_options,
    default_quality_options,
)
from .settings import Settings
from .utils import (
    FFmpegStatus,
    classify_url,
    detect_ffmpeg,
    ensure_dir,
    human_bytes,
    human_duration,
    is_writable_dir,
    normalize_url,
    validate_url,
)
from .workers import AnalyzeWorker, DownloadWorker, YtdlpUpdateWorker

COL_CHECK, COL_INDEX, COL_TITLE, COL_DURATION, COL_STATUS = range(5)

STYLESHEET = """
QWidget {
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 10pt;
    color: #101a2f;
}
#Root { background: #eef2fa; }
#Content { background: transparent; }

/* One background everywhere: no OS dark-mode black, no stark white gaps. */
QMainWindow, QDialog { background: #eef2fa; }
QMessageBox { background: #f6f9ff; }
QMessageBox QLabel { color: #101a2f; background: transparent; }
QMessageBox QLabel#qt_msgbox_label { color: #101a2f; }
QMessageBox QLabel#qt_msgbox_informativelabel { color: #475569; font-size: 9pt; }
QMessageBox QPushButton { min-width: 78px; }

/* DetailsDialog: painted light background, explicit text colours. */
#DlgHeadline { color: #0b1220; font-size: 12pt; font-weight: 800; }
#DlgMessage { color: #33415c; }
#DlgHint { color: #6b7a93; font-size: 9pt; }
QPushButton#DlgOk {
    background: #2563eb;
    color: #ffffff;
    border: 0;
    border-radius: 8px;
    padding: 8px 22px;
    font-weight: 700;
}
QPushButton#DlgOk:hover { background: #1d4ed8; }
QPushButton#DlgOk:pressed { background: #1e40af; }
QLabel { background: transparent; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }
QTableWidget QTableCornerButton::section { background: #f5f8ff; border: 0; }
#AccentBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #4f8cff, stop:0.55 #2563eb, stop:1 #a855f7);
}
#Header {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #f6f9ff);
    border-bottom: 1px solid #dfe7f4;
}
#AppTitle { font-size: 14pt; font-weight: 800; color: #0b1220; }
#AppSubtitle { color: #6b7a93; font-size: 9pt; }

QMenuBar { background: #ffffff; border-bottom: 1px solid #e6ecf7; padding: 2px 6px; }
QMenuBar::item { background: transparent; padding: 5px 9px; border-radius: 6px; color: #33415c; }
QMenuBar::item:selected { background: #eaf1ff; color: #1d4ed8; }
QMenu { background: #ffffff; border: 1px solid #dfe7f4; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 24px 6px 20px; border-radius: 6px; color: #33415c; }
QMenu::item:selected { background: #eef4ff; color: #1d4ed8; }
QMenu::separator { height: 1px; background: #e9eef8; margin: 5px 8px; }

QFrame#Card { background: #ffffff; border: 1px solid #e0e8f5; border-radius: 16px; }
QLabel#Hint { color: #6b7a93; }
QLabel#HintError { color: #d92d20; }
QLabel#HintOk { color: #0f7a5a; }
QLabel#Section { color: #475569; font-weight: 600; }
QLabel#Current { font-weight: 700; color: #0b1220; }

QLineEdit {
    background: #ffffff;
    border: 1px solid #d7deec;
    border-radius: 10px;
    padding: 10px 12px;
    min-height: 22px;
    color: #101a2f;
    selection-background-color: #dbe8ff;
    selection-color: #0b1220;
}
QLineEdit:hover { border-color: #bcc9e2; }
QLineEdit:focus { border: 1px solid #3b6ff5; background: #fbfdff; }

QComboBox {
    background: #ffffff;
    border: 1px solid #d7deec;
    border-radius: 10px;
    padding: 7px 10px;
    min-height: 20px;
    color: #101a2f;
}
QComboBox:hover { border-color: #bcc9e2; }
QComboBox:focus { border: 1px solid #3b6ff5; }
QComboBox:disabled { background: #f4f7fd; color: #9aa8c2; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView {
    border: 1px solid #dfe7f4;
    border-radius: 10px;
    background: #ffffff;
    color: #101a2f;
    selection-background-color: #eaf1ff;
    selection-color: #1d4ed8;
    padding: 4px;
    outline: 0;
}

QPushButton {
    background: #ffffff;
    border: 1px solid #d7deec;
    border-radius: 10px;
    padding: 8px 14px;
    color: #33415c;
}
QPushButton:hover { background: #f6f9ff; border-color: #bcc9e2; }
QPushButton:pressed { background: #eaf1ff; }
QPushButton:disabled { color: #9aa8c2; border-color: #e6ecf7; background: #f6f9ff; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4f8cff, stop:1 #2563eb);
    color: #ffffff;
    border: 0;
    border-radius: 10px;
    font-weight: 700;
    padding: 10px 22px;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3f7fff, stop:1 #1d4ed8);
}
QPushButton#Primary:pressed { background: #1e40af; }
QPushButton#Primary:disabled { background: #b9cdf7; color: #ffffff; }
QPushButton#Flat {
    border: 0;
    background: transparent;
    color: #475569;
    padding: 7px 10px;
}
QPushButton#Flat:hover { background: #eaf1ff; color: #1d4ed8; border-radius: 9px; }
QPushButton#Link {
    border: 0;
    background: transparent;
    color: #2563eb;
    padding: 4px 6px;
    font-weight: 600;
}
QPushButton#Link:hover { color: #4f46e5; }
QPushButton#Seg {
    background: #f2f6fe;
    border: 1px solid #e2eaf7;
    border-radius: 10px;
    padding: 7px 18px;
    color: #475569;
    font-weight: 600;
}
QPushButton#Seg:hover { background: #eaf1ff; border-color: #cddcf5; }
QPushButton#Seg:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #4f46e5);
    color: #ffffff;
    border-color: #3b5bdb;
}
QPushButton#Seg:checked:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1d4ed8, stop:1 #4338ca);
}
QPushButton#Seg:disabled { color: #a9b5cc; background: #f6f8fd; border-color: #edf2fb; }

QProgressBar {
    border: 0;
    background: #e6edf9;
    border-radius: 6px;
    min-height: 12px;
    max-height: 12px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #4f8cff, stop:0.5 #2563eb, stop:1 #a855f7);
    border-radius: 6px;
}

QTableWidget {
    background: #ffffff;
    border: 1px solid #e0e8f5;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: #eef4ff;
    selection-color: #0b1220;
    outline: 0;
}
QTableWidget::item { padding: 4px 6px; border-bottom: 1px solid #f2f6fd; }
QHeaderView::section {
    background: #f5f8ff;
    border: 0;
    border-bottom: 1px solid #e0e8f5;
    padding: 7px 6px;
    color: #6b7a93;
    font-weight: 700;
    font-size: 8.5pt;
}

QCheckBox { color: #475569; spacing: 8px; }

QPlainTextEdit {
    background: #0b1220;
    color: #cfdcf3;
    border: 1px solid #1b2a44;
    border-radius: 12px;
    padding: 10px;
    selection-background-color: #2a3f63;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 9pt;
}

QStatusBar { background: #ffffff; border-top: 1px solid #e0e8f5; color: #6b7a93; }
QStatusBar::item { border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c3cee2; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #a9b8d4; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c3cee2; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip {
    background: #0b1220;
    color: #f4f8ff;
    border: 1px solid #26344f;
    border-radius: 6px;
    padding: 6px 8px;
}
"""


def build_palette() -> QPalette:
    """Return the app-wide light palette used by every widget and dialog."""
    palette = QPalette()
    window = QColor("#eef2fa")
    base = QColor("#ffffff")
    text = QColor("#101a2f")
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f6f9ff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#0b1220"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f4f8ff"))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#93a1b8"))
    palette.setColor(QPalette.ColorRole.Button, base)
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#33415c"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#d92d20"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    muted = QColor("#9aa8c2")
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#f6f9ff"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor("#f6f9ff"))
    return palette


def apply_theme(app: QApplication) -> None:
    """Apply one consistent theme, independent of the OS light/dark setting.

    Without this the widgets that QSS does not cover (menus, message boxes,
    tooltips, scroll areas) inherit the system palette, which is why parts of
    the window could look black on a dark-themed Windows install.
    """
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(STYLESHEET)


class DetailsDialog(QDialog):
    """An always-readable message dialog.

    ``QMessageBox`` takes its frame and background from the platform style, so
    on a dark Windows theme its body could come out dark while the app's
    stylesheet still forced near-black text: the message was there but
    invisible. This dialog paints its own light background and sets an
    explicit colour on every label, so a download failure is always legible.
    """

    BACKGROUND = QColor("#f6f9ff")

    def __init__(
        self,
        parent: Optional[QWidget],
        *,
        headline: str,
        message: str,
        hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setModal(True)
        self.setAutoFillBackground(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(620)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self.BACKGROUND)
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#101a2f"))
        self.setPalette(palette)

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        if not icon.isNull():
            glyph = QLabel()
            glyph.setPixmap(icon.pixmap(28, 28))
            glyph.setFixedSize(30, 30)
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        headline_label = QLabel(headline)
        headline_label.setObjectName("DlgHeadline")
        headline_label.setWordWrap(True)
        head.addWidget(headline_label, 1)
        layout.addLayout(head)

        body = QLabel(message)
        body.setObjectName("DlgMessage")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(240)
        scroll.setWidget(body)
        layout.addWidget(scroll)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("DlgHint")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton("OK")
        ok.setObjectName("DlgOk")
        ok.setDefault(True)
        ok.setAutoDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        self._headline = headline
        self._message = message

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Fill the window with the light background, whatever the OS theme."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.BACKGROUND)
        painter.end()


def failure_report(summary: Dict[str, Any]) -> tuple[str, str, str]:
    """Build ``(headline, message, hint)`` for a failed download summary."""
    failed_results = [
        r for r in summary.get("results", []) if r.get("status") == STATUS_FAILED
    ]
    lines = []
    for result in failed_results:
        title = (result.get("title") or result.get("url") or "This video").strip()
        reason = (result.get("error") or "Unknown error").strip()
        lines.append(f"{title}\n    {reason}")
    count = len(failed_results) or int(summary.get("failed", 0) or 0)
    headline = "1 download failed" if count == 1 else f"{count} downloads failed"
    completed = int(summary.get("completed", 0) or 0)
    hint_parts = []
    if completed:
        hint_parts.append(f"{completed} downloaded successfully.")
    hint_parts.append("Pressing Download again retries the failed items.")
    hint_parts.append("File ▸ Open logs folder keeps the full yt-dlp log.")
    message = "\n\n".join(lines) or "The download did not produce a file."
    return headline, message, " ".join(hint_parts)


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None, ffmpeg: Optional[FFmpegStatus] = None):
        super().__init__()
        self.settings = settings or Settings()
        self.ffmpeg: FFmpegStatus = ffmpeg or detect_ffmpeg(
            self.settings.get("ffmpeg_location") or None
        )

        self._analysis: Optional[Dict[str, Any]] = None
        self._analysis_url: str = ""
        self._entries: List[Dict[str, Any]] = []
        self._formats: List[Dict[str, Any]] = []
        self._last_summary: Optional[Dict[str, Any]] = None
        self._pending_download = False
        self._download_playlist = False

        self._analyze_thread: Optional[QThread] = None
        self._analyze_worker: Optional[AnalyzeWorker] = None
        self._dl_thread: Optional[QThread] = None
        self._dl_worker: Optional[DownloadWorker] = None
        self._ytdlp_thread: Optional[QThread] = None
        self._ytdlp_worker: Optional[YtdlpUpdateWorker] = None
        self._ytdlp_manual = False

        self._total_tasks = 0
        self._finished_tasks = 0
        self._counts = {"completed": 0, "skipped": 0, "failed": 0, "cancelled": 0}

        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(QIcon(self._make_logo_pixmap(64)))
        self.resize(760, 480)
        self.setMinimumSize(640, 400)

        self._build_menu()
        self._build_ui()
        self._apply_settings()
        self._check_ffmpeg(initial=True)
        # Check PyPI for a newer yt-dlp once the window is up; silent unless it
        # installs something.
        QTimer.singleShot(1200, self._auto_update_ytdlp)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        act_folder = QAction("Change download folder…", self)
        act_folder.triggered.connect(self._on_browse)
        file_menu.addAction(act_folder)

        act_ffmpeg = QAction("Set FFmpeg location…", self)
        act_ffmpeg.triggered.connect(self._choose_ffmpeg_location)
        file_menu.addAction(act_ffmpeg)

        act_logs = QAction("Open logs folder", self)
        act_logs.triggered.connect(self._open_logs_folder)
        file_menu.addAction(act_logs)

        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        help_menu = self.menuBar().addMenu("&Help")
        self.act_ytdlp_update = QAction("Check for yt-dlp update…", self)
        self.act_ytdlp_update.triggered.connect(lambda: self._start_ytdlp_update(manual=True))
        help_menu.addAction(self.act_ytdlp_update)

        self.act_ytdlp_rollback = QAction("Roll back yt-dlp update", self)
        self.act_ytdlp_rollback.triggered.connect(self._rollback_ytdlp)
        help_menu.addAction(self.act_ytdlp_rollback)
        help_menu.addSeparator()

        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    @staticmethod
    def _make_logo_pixmap(size: int = 40) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QColor("#4f8cff"))
        grad.setColorAt(1.0, QColor("#7c3aed"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(0, 0, size, size, size * 0.28, size * 0.28)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(max(1.6, size * 0.085))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(int(size * 0.5), int(size * 0.28), int(size * 0.5), int(size * 0.58))
        p.drawLine(int(size * 0.36), int(size * 0.46), int(size * 0.5), int(size * 0.6))
        p.drawLine(int(size * 0.64), int(size * 0.46), int(size * 0.5), int(size * 0.6))
        p.drawLine(int(size * 0.3), int(size * 0.73), int(size * 0.7), int(size * 0.73))
        p.end()
        return pm

    def _build_ui(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        accent = QFrame()
        accent.setObjectName("AccentBar")
        accent.setFixedHeight(4)
        root.addWidget(accent)

        # ---- Header --------------------------------------------------- #
        header = QFrame()
        header.setObjectName("Header")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(22, 15, 22, 15)
        hb.setSpacing(13)
        logo = QLabel()
        logo.setPixmap(self._make_logo_pixmap(40))
        logo.setFixedSize(40, 40)
        hb.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        subtitle = QLabel("Paste a link, press Download.")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        hb.addLayout(titles)
        hb.addStretch(1)
        root.addWidget(header)

        # ---- Body (scrolls when the window is short) ------------------- #
        content = QWidget()
        content.setObjectName("Content")
        body = QVBoxLayout(content)
        body.setContentsMargins(22, 20, 22, 18)
        body.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(11)

        # Link + Download is the single primary action.
        link_row = QHBoxLayout()
        link_row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste a YouTube video or playlist link")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self._on_start)
        self.url_input.textChanged.connect(self._on_url_changed)
        self.btn_paste = QPushButton("Paste")
        self.btn_paste.setObjectName("Flat")
        self.btn_start = QPushButton("Download")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setMinimumWidth(130)
        self.btn_paste.clicked.connect(self._on_paste)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_start.setDefault(True)
        link_row.addWidget(self.url_input, 1)
        link_row.addWidget(self.btn_paste)
        link_row.addWidget(self.btn_start)
        card_lay.addLayout(link_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("Hint")
        self.status_hint.setWordWrap(True)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("Flat")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        status_row.addWidget(self.status_hint, 1)
        status_row.addWidget(self.btn_cancel)
        card_lay.addLayout(status_row)

        # Format row: video / mp3, quality, advanced toggle.
        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        self.format_group = QButtonGroup(self)
        self.format_group.setExclusive(True)
        self.btn_video = QPushButton("Video")
        self.btn_audio = QPushButton("MP3")
        for btn in (self.btn_video, self.btn_audio):
            btn.setObjectName("Seg")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.format_group.addButton(btn)
            format_row.addWidget(btn)
        self.btn_video.setChecked(True)
        self.btn_video.toggled.connect(self._on_format_changed)
        self.btn_audio.toggled.connect(self._on_format_changed)

        self.quality_combo = QComboBox()
        self.quality_combo.setMinimumWidth(210)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        format_row.addWidget(self.quality_combo)
        format_row.addStretch(1)

        self.btn_more = QPushButton("More options")
        self.btn_more.setObjectName("Link")
        self.btn_more.setCheckable(True)
        self.btn_more.toggled.connect(self._on_toggle_more)
        format_row.addWidget(self.btn_more)
        card_lay.addLayout(format_row)

        # Save-to line.
        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self.lbl_saved = QLabel("")
        self.lbl_saved.setObjectName("Hint")
        self.btn_change = QPushButton("Change")
        self.btn_change.setObjectName("Link")
        self.btn_open = QPushButton("Open")
        self.btn_open.setObjectName("Link")
        self.btn_change.clicked.connect(self._on_browse)
        self.btn_open.clicked.connect(self._open_output_folder)
        save_row.addWidget(self.lbl_saved, 1)
        save_row.addWidget(self.btn_change)
        save_row.addWidget(self.btn_open)
        card_lay.addLayout(save_row)

        # ---- More options (advanced, hidden by default) --------------- #
        self.more_panel = QWidget()
        more_lay = QVBoxLayout(self.more_panel)
        more_lay.setContentsMargins(0, 4, 0, 0)
        more_lay.setSpacing(8)
        self.chk_skip_existing = QCheckBox("Skip files that already exist")
        self.chk_playlist_folder = QCheckBox("Separate folder per playlist")
        more_lay.addWidget(self.chk_skip_existing)
        more_lay.addWidget(self.chk_playlist_folder)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(120)
        self.log_view.setVisible(False)
        more_lay.addWidget(self.log_view)
        self.btn_log = QPushButton("Show details")
        self.btn_log.setObjectName("Link")
        self.btn_log.setCheckable(True)
        self.btn_log.toggled.connect(self._on_toggle_log)
        btn_log_row = QHBoxLayout()
        btn_log_row.addWidget(self.btn_log)
        btn_log_row.addStretch(1)
        more_lay.addLayout(btn_log_row)
        self.more_panel.setVisible(False)
        card_lay.addWidget(self.more_panel)

        # ---- Playlist checklist (only for playlist links) ------------- #
        self.playlist_panel = QWidget()
        pl_lay = QVBoxLayout(self.playlist_panel)
        pl_lay.setContentsMargins(0, 2, 0, 0)
        pl_lay.setSpacing(8)
        pl_head = QHBoxLayout()
        pl_head.setSpacing(6)
        self.lbl_playlist = QLabel("")
        self.lbl_playlist.setObjectName("Section")
        self.btn_playlist_all = QPushButton("Download playlist")
        self.btn_playlist_all.setObjectName("Link")
        self.btn_all = QPushButton("All")
        self.btn_all.setObjectName("Link")
        self.btn_none = QPushButton("None")
        self.btn_none.setObjectName("Link")
        self.btn_all.clicked.connect(lambda: self._set_all_checks(True))
        self.btn_none.clicked.connect(lambda: self._set_all_checks(False))
        self.btn_playlist_all.clicked.connect(self._on_download_playlist)
        pl_head.addWidget(self.lbl_playlist, 1)
        pl_head.addWidget(self.btn_playlist_all)
        pl_head.addWidget(self.btn_all)
        pl_head.addWidget(self.btn_none)
        pl_lay.addLayout(pl_head)

        self.playlist_table = QTableWidget(0, 5)
        self.playlist_table.setHorizontalHeaderLabels(["", "#", "Title", "Length", "Status"])
        hdr = self.playlist_table.horizontalHeader()
        hdr.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for c in (COL_CHECK, COL_INDEX, COL_DURATION, COL_STATUS):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.playlist_table.verticalHeader().setVisible(False)
        self.playlist_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.playlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.playlist_table.setMinimumHeight(170)
        pl_lay.addWidget(self.playlist_table)
        self.playlist_panel.setVisible(False)
        card_lay.addWidget(self.playlist_panel)

        # ---- Progress (only while downloading) ------------------------ #
        self.progress_panel = QWidget()
        pr_lay = QVBoxLayout(self.progress_panel)
        pr_lay.setContentsMargins(0, 2, 0, 0)
        pr_lay.setSpacing(6)
        self.lbl_current = QLabel("Ready")
        self.lbl_current.setObjectName("Current")
        self.bar_current = QProgressBar()
        self.bar_current.setRange(0, 1000)
        self.bar_current.setValue(0)
        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("Hint")
        pr_lay.addWidget(self.lbl_current)
        pr_lay.addWidget(self.bar_current)
        pr_lay.addWidget(self.lbl_stats)
        self.progress_panel.setVisible(False)
        card_lay.addWidget(self.progress_panel)

        body.addWidget(card)

        self.ffmpeg_banner = QLabel()
        self.ffmpeg_banner.setWordWrap(True)
        self.ffmpeg_banner.setVisible(False)
        body.addWidget(self.ffmpeg_banner)

        body.addStretch(1)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.statusBar().showMessage("Ready")
        self.ytdlp_indicator = QLabel()
        self.ytdlp_indicator.setStyleSheet("color:#475569;")
        self._refresh_ytdlp_indicator()
        self.statusBar().addPermanentWidget(self.ytdlp_indicator)
        self.ffmpeg_indicator = QLabel()
        self.statusBar().addPermanentWidget(self.ffmpeg_indicator)

        for btn in (self.btn_paste, self.btn_start, self.btn_cancel, self.btn_video,
                    self.btn_audio, self.btn_change, self.btn_open, self.btn_more,
                    self.btn_all, self.btn_none, self.btn_log, self.btn_playlist_all):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._update_saved_label()
        self._update_button_states()

    # ------------------------------------------------------------------ #
    # Small UI toggles
    # ------------------------------------------------------------------ #
    @Slot(bool)
    def _on_toggle_more(self, checked: bool) -> None:
        self.more_panel.setVisible(checked)
        self.btn_more.setText("Hide options" if checked else "More options")

    @Slot(bool)
    def _on_toggle_log(self, checked: bool) -> None:
        self.log_view.setVisible(checked)
        self.btn_log.setText("Hide details" if checked else "Show details")

    def _update_saved_label(self) -> None:
        path = self.output_edit_path()
        self.lbl_saved.setText(f"Saved to  {path}")
        self.lbl_saved.setToolTip(path)

    def output_edit_path(self) -> str:
        return self.settings.get("output_dir") or ""

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #
    def _apply_settings(self) -> None:
        audio_mode = self.settings.get("output_mode", "video") == "audio"
        if audio_mode:
            self.btn_audio.setChecked(True)
        else:
            self.btn_video.setChecked(True)
        self.chk_skip_existing.setChecked(bool(self.settings.get("skip_existing", True)))
        self.chk_playlist_folder.setChecked(bool(self.settings.get("make_playlist_folder", True)))
        self._reload_quality_combo(
            default_quality_options(self.ffmpeg.usable),
            self.settings.get("quality", QUALITY_BEST),
        )
        self._on_format_changed()

        geo = self.settings.get("window_geometry")
        if geo:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass

    def _save_settings(self) -> None:
        import base64
        geometry = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        self.settings.update({
            "output_dir": self.output_edit_path(),
            "quality": self.quality_combo.currentData() or QUALITY_BEST,
            "output_mode": "audio" if self.btn_audio.isChecked() else "video",
            "make_playlist_folder": self.chk_playlist_folder.isChecked(),
            "skip_existing": self.chk_skip_existing.isChecked(),
            "window_geometry": geometry,
        })
        self.settings.save()

    # ------------------------------------------------------------------ #
    # FFmpeg
    # ------------------------------------------------------------------ #
    def _check_ffmpeg(self, initial: bool = False) -> None:
        st = self.ffmpeg
        if st.usable:
            self.ffmpeg_banner.setVisible(False)
            self.ffmpeg_indicator.setText("● FFmpeg ready")
            self.ffmpeg_indicator.setStyleSheet("color:#0f7a5a; font-weight:600;")
            self.ffmpeg_indicator.setToolTip(st.describe())
        else:
            if st.broken:
                # The binaries exist but do not run (truncated copy, a package
                # manager shim, or a missing DLL). Saying "not found" here
                # sends people looking in the wrong place.
                self.ffmpeg_banner.setText(
                    "FFmpeg was found but could not run, so high-resolution "
                    "merging and MP3 conversion are unavailable. Downloads "
                    "fall back to single-file formats. Point File ▸ Set FFmpeg "
                    "location… at a working ffmpeg.exe/ffprobe.exe folder, or "
                    "install it with winget install Gyan.FFmpeg"
                )
            else:
                self.ffmpeg_banner.setText(
                    "FFmpeg not found: high-resolution video (separate audio + "
                    "video streams) and MP3 conversion are unavailable. "
                    "Downloads use single-file formats instead. Install it "
                    "with winget install Gyan.FFmpeg, or set its location in "
                    "File ▸ Set FFmpeg location…"
                )
            self.ffmpeg_banner.setStyleSheet(
                "background:#fff7ed; color:#9a3412; border:1px solid #fed7aa;"
                "border-radius:10px; padding:10px 12px;"
            )
            self.ffmpeg_banner.setVisible(True)
            self.ffmpeg_indicator.setText(
                "● FFmpeg broken" if st.broken else "● FFmpeg missing"
            )
            self.ffmpeg_indicator.setStyleSheet("color:#d92d20; font-weight:600;")
            self.ffmpeg_indicator.setToolTip(st.describe())
        if not initial:
            self._reload_quality_combo(
                default_quality_options(st.usable),
                self.quality_combo.currentData() or QUALITY_BEST,
            )
            self.btn_audio.setEnabled(st.usable)

    def _choose_ffmpeg_location(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select the folder containing ffmpeg/ffprobe")
        if not path:
            return
        self.settings.set("ffmpeg_location", path)
        self.settings.save()
        self.ffmpeg = detect_ffmpeg(path)
        self._check_ffmpeg()
        self._log(f"FFmpeg re-detected: {self.ffmpeg.describe()}")
        self.btn_audio.setEnabled(self.ffmpeg.usable)

    # ------------------------------------------------------------------ #
    # yt-dlp updates
    # ------------------------------------------------------------------ #
    def _ytdlp_label(self, status: Optional[Dict[str, Any]] = None) -> str:
        status = status or ytdlp_updater.active_status()
        version = status.get("version") or "unknown"
        source = "updated" if status.get("source") == "updated" else "bundled"
        return f"yt-dlp {version} · {source}"

    def _refresh_ytdlp_indicator(self, text: Optional[str] = None) -> None:
        status = ytdlp_updater.active_status()
        self.ytdlp_indicator.setText(text or self._ytdlp_label(status))
        tip = [f"yt-dlp {status.get('version') or 'unknown'} ({status.get('source')})"]
        if status.get("path"):
            tip.append(str(status["path"]))
        tip.append("Help \u25b8 Check for yt-dlp update\u2026 installs a newer copy for this account.")
        tip.append("Help \u25b8 Roll back yt-dlp update returns to the previous or bundled copy.")
        self.ytdlp_indicator.setToolTip("\n".join(tip))

    def _auto_update_ytdlp(self) -> None:
        if not self.settings.get("auto_update_ytdlp", True):
            return
        if self._ytdlp_thread is not None or not ytdlp_updater.check_due():
            return
        self._start_ytdlp_update(manual=False)

    def _start_ytdlp_update(self, manual: bool) -> None:
        if self._ytdlp_thread is not None:
            return
        if self._dl_thread is not None:
            if manual:
                self._set_hint("A download is running; check for updates when it finishes.", "Hint")
            return
        self._ytdlp_manual = manual
        self.act_ytdlp_update.setEnabled(False)
        self.act_ytdlp_rollback.setEnabled(False)
        if manual:
            self.statusBar().showMessage("Checking PyPI for a newer yt-dlp\u2026")
        self._log("Checking PyPI for a yt-dlp update\u2026")
        self._ytdlp_thread = QThread(self)
        self._ytdlp_worker = YtdlpUpdateWorker(force=manual)
        self._ytdlp_worker.moveToThread(self._ytdlp_thread)
        self._ytdlp_thread.started.connect(self._ytdlp_worker.run)
        self._ytdlp_worker.log.connect(self._log)
        self._ytdlp_worker.finished.connect(self._on_ytdlp_update_finished)
        self._ytdlp_worker.finished.connect(self._ytdlp_thread.quit)
        self._ytdlp_thread.finished.connect(self._cleanup_ytdlp_update)
        self._ytdlp_thread.start()

    @Slot(dict)
    def _on_ytdlp_update_finished(self, result: Dict[str, Any]) -> None:
        action = result.get("action")
        latest = result.get("latest") or result.get("version")
        if action == "installed":
            message = f"yt-dlp {latest} was installed for this user. Restart {APP_NAME} to use it."
            self._refresh_ytdlp_indicator(text=f"yt-dlp {latest} · restart to use")
            self.statusBar().showMessage(message, 15000)
            if self._ytdlp_manual:
                QMessageBox.information(
                    self, "yt-dlp updated",
                    message + "\n\nHelp \u25b8 Roll back yt-dlp update returns to the copy that "
                    "was working before.",
                )
        elif action == "up-to-date":
            self.statusBar().showMessage("yt-dlp is up to date.", 6000)
            if self._ytdlp_manual:
                QMessageBox.information(
                    self, "yt-dlp is up to date", result.get("message") or ""
                )
        elif action == "blocked":
            if self._ytdlp_manual:
                QMessageBox.information(self, "yt-dlp update", result.get("message") or "")
        elif action in ("error", "none"):
            detail = result.get("error") or result.get("message") or "Unknown error"
            if self._ytdlp_manual:
                QMessageBox.warning(self, "yt-dlp update failed", detail)
            else:
                self._log(f"yt-dlp update check skipped: {detail}")
        self._log(f"yt-dlp: {result.get('message') or result.get('error') or action}")

    @Slot()
    def _cleanup_ytdlp_update(self) -> None:
        if self._ytdlp_worker:
            self._ytdlp_worker.deleteLater()
        if self._ytdlp_thread:
            self._ytdlp_thread.deleteLater()
        self._ytdlp_worker = None
        self._ytdlp_thread = None
        self.act_ytdlp_update.setEnabled(True)
        self.act_ytdlp_rollback.setEnabled(True)

    def _rollback_ytdlp(self) -> None:
        store = ytdlp_updater.default_store()
        has_previous = (store.previous / "yt_dlp" / "__init__.py").is_file()
        question = (
            "Restore the yt-dlp copy that was in use before the last update?"
            if has_previous
            else "Remove the downloaded yt-dlp and go back to the copy bundled with this app?"
        )
        reply = QMessageBox.question(
            self, "Roll back yt-dlp",
            question + "\n\nRestart the app afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = ytdlp_updater.rollback()
        if result.get("ok"):
            self._log(f"yt-dlp rollback: {result.get('message')}")
            self._refresh_ytdlp_indicator(text="yt-dlp · restart to apply")
            QMessageBox.information(
                self, "yt-dlp rolled back",
                f"{result.get('message')}\n\nRestart {APP_NAME} to apply it.",
            )
        else:
            QMessageBox.warning(
                self, "yt-dlp rollback",
                result.get("error") or "There is nothing to roll back.",
            )

    # ------------------------------------------------------------------ #
    # URL / analysis
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_paste(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text)
        else:
            self.status_hint.setText("Clipboard is empty.")
            self.status_hint.setObjectName("Hint")
            self._restyle_hint("Hint")

    @Slot(str)
    def _on_url_changed(self, text: str) -> None:
        if not text.strip():
            self._set_hint("", "")
            return
        ok, err = validate_url(text)
        if not ok:
            self._set_hint(err or "Invalid link.", "HintError")
            return
        kind = classify_url(text)
        if kind == KIND_PLAYLIST:
            self._set_hint("Playlist link — press Download to fetch the list.", "Hint")
        elif kind == KIND_VIDEO_IN_PLAYLIST:
            self._set_hint("Video in a playlist — press Download for this video.", "Hint")
        elif kind == KIND_VIDEO:
            self._set_hint("Ready to download.", "HintOk")
        else:
            self._set_hint(err or "Invalid link.", "HintError")

    def _set_hint(self, text: str, style: str) -> None:
        self.status_hint.setText(text)
        self._restyle_hint(style)

    def _restyle_hint(self, style: str) -> None:
        self.status_hint.setObjectName(style or "Hint")
        self.status_hint.style().unpolish(self.status_hint)
        self.status_hint.style().polish(self.status_hint)

    @Slot()
    def _on_start(self) -> None:
        if self._dl_thread is not None or self._analyze_thread is not None:
            return
        url = normalize_url(self.url_input.text())
        ok, err = validate_url(url)
        if not ok:
            QMessageBox.warning(self, "Invalid link", err or "Invalid link.")
            return
        if self._analysis is None or self._analysis_url != url:
            self._pending_download = True
            self._start_analysis(url)
            return
        self._start_download(self._collect_tasks())

    def _start_analysis(self, url: str) -> None:
        self.btn_start.setEnabled(False)
        self.btn_start.setText("Preparing…")
        self._set_hint("Reading link…", "Hint")
        self._log(f"Analyzing URL: {url}")

        self._analyze_thread = QThread(self)
        self._analyze_worker = AnalyzeWorker(url, ffmpeg_status=self.ffmpeg)
        self._analyze_worker.moveToThread(self._analyze_thread)
        self._analyze_thread.started.connect(self._analyze_worker.run)
        self._analyze_worker.log.connect(self._log)
        self._analyze_worker.finished.connect(self._on_analysis_finished)
        self._analyze_worker.failed.connect(self._on_analysis_failed)
        self._analyze_worker.finished.connect(self._analyze_thread.quit)
        self._analyze_worker.failed.connect(self._analyze_thread.quit)
        self._analyze_thread.finished.connect(self._cleanup_analyze)
        self._analyze_thread.start()

    @Slot(dict)
    def _on_analysis_finished(self, payload: Dict[str, Any]) -> None:
        self._analysis = payload
        self._analysis_url = payload.get("url") or normalize_url(self.url_input.text())
        kind = payload.get("kind")
        self._formats = payload.get("formats") or []

        if kind == KIND_VIDEO:
            self._set_hint(f"{payload.get('title') or 'Video'}  •  "
                           f"{human_duration(payload.get('duration'))}", "HintOk")
        else:
            title = payload.get("playlist_title") or payload.get("title") or "Playlist"
            self._set_hint(f"{title}  •  {len(payload.get('entries') or [])} videos", "HintOk")

        self._populate_playlist(payload.get("entries") or [])
        self._download_playlist = False

        if self._formats and kind == KIND_VIDEO:
            self._reload_quality_combo(
                available_quality_options(self._formats, self.ffmpeg.usable),
                self.quality_combo.currentData() or QUALITY_BEST,
            )
        else:
            self._reload_quality_combo(
                default_quality_options(self.ffmpeg.usable),
                self.quality_combo.currentData() or QUALITY_BEST,
            )
        self._log("Analysis complete.")

        if self._pending_download:
            self._pending_download = False
            self._start_download(self._collect_tasks())

    @Slot(str)
    def _on_analysis_failed(self, message: str) -> None:
        self._pending_download = False
        self._set_hint(f"Could not read the link: {message}", "HintError")
        self._log(f"Analysis failed: {message}")
        self._update_button_states()

    @Slot()
    def _cleanup_analyze(self) -> None:
        if self._analyze_worker:
            self._analyze_worker.deleteLater()
        if self._analyze_thread:
            self._analyze_thread.deleteLater()
        self._analyze_worker = None
        self._analyze_thread = None
        self._update_button_states()

    # ------------------------------------------------------------------ #
    # Playlist list
    # ------------------------------------------------------------------ #
    def _populate_playlist(self, entries: List[Dict[str, Any]]) -> None:
        self._entries = list(entries)
        self.playlist_table.setRowCount(0)
        self.playlist_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            available = bool(entry.get("available", True))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if available else Qt.CheckState.Unchecked)
            if not available:
                chk.setFlags(Qt.ItemFlag.NoItemFlags)
            self.playlist_table.setItem(row, COL_CHECK, chk)

            idx = QTableWidgetItem(str(entry.get("index", row + 1)))
            idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.playlist_table.setItem(row, COL_INDEX, idx)

            self.playlist_table.setItem(row, COL_TITLE, QTableWidgetItem(entry.get("title", "")))

            dur = QTableWidgetItem(human_duration(entry.get("duration")))
            dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.playlist_table.setItem(row, COL_DURATION, dur)

            status = QTableWidgetItem("—" if available else "Unavailable")
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not available:
                status.setForeground(QColor("#8a1c1c"))
                status.setToolTip(entry.get("reason", ""))
            self.playlist_table.setItem(row, COL_STATUS, status)

        has_entries = bool(entries)
        self.playlist_panel.setVisible(has_entries)
        self.lbl_playlist.setText(f"{len(entries)} videos")
        for btn in (self.btn_all, self.btn_none):
            btn.setEnabled(has_entries)

    @Slot()
    def _on_download_playlist(self) -> None:
        """Download every available entry, even for a video-in-playlist link."""
        self._download_playlist = True
        self._start_download(self._collect_tasks())

    def _set_all_checks(self, checked: bool) -> None:
        for row, entry in enumerate(self._entries):
            if not entry.get("available", True):
                continue
            item = self.playlist_table.item(row, COL_CHECK)
            if item:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _checked_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        for row, entry in enumerate(self._entries):
            item = self.playlist_table.item(row, COL_CHECK)
            if item and item.checkState() == Qt.CheckState.Checked and entry.get("available", True):
                tasks.append({"url": entry["url"], "title": entry.get("title", ""),
                              "index": entry.get("index", row + 1)})
        return tasks

    # ------------------------------------------------------------------ #
    # Format / quality
    # ------------------------------------------------------------------ #
    def _is_audio_mode(self) -> bool:
        return self.btn_audio.isChecked()

    @Slot()
    def _on_format_changed(self) -> None:
        audio = self._is_audio_mode()
        self.quality_combo.setEnabled(not audio)
        self.btn_audio.setEnabled(self.ffmpeg.usable)
        if audio and not self.ffmpeg.usable:
            self.btn_video.setChecked(True)
            return
        self._on_quality_changed()

    def _reload_quality_combo(self, options: List[tuple], preferred: str) -> None:
        self.quality_combo.blockSignals(True)
        self.quality_combo.clear()
        for key, label in options:
            self.quality_combo.addItem(label, key)
        idx = self.quality_combo.findData(preferred)
        self.quality_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.quality_combo.blockSignals(False)

    @Slot()
    def _on_quality_changed(self) -> None:
        if self._is_audio_mode():
            return
        if not self._analysis:
            return
        quality = self.quality_combo.currentData() or QUALITY_BEST
        label = self.quality_combo.currentText()
        if quality == QUALITY_BEST:
            return
        self._set_hint(f"Quality: {label}", "Hint")

    def _current_quality(self) -> str:
        if self._is_audio_mode():
            return QUALITY_AUDIO_MP3
        return self.quality_combo.currentData() or QUALITY_BEST

    # ------------------------------------------------------------------ #
    # Output folder
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_browse(self) -> None:
        start = self.output_edit_path() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select download folder", start)
        if path:
            self.settings.set("output_dir", path)
            self.settings.save()
            self._update_saved_label()

    @Slot()
    def _open_output_folder(self) -> None:
        path = self.output_edit_path()
        if not path:
            QMessageBox.information(self, "No folder", "Choose a download folder first.")
            return
        try:
            ensure_dir(path)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot open folder", str(exc))
            return
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - user chosen path
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def _open_logs_folder(self) -> None:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(log_dir))  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(log_dir)])

    def _show_about(self) -> None:
        yt = ytdlp_updater.active_status()
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "A PySide6 + yt-dlp downloader for YouTube videos and playlists.<br>"
            "Download only content you are authorised to download.<br><br>"
            f"<b>FFmpeg:</b> {self.ffmpeg.describe().replace(chr(10), '<br>')}<br><br>"
            f"<b>yt-dlp:</b> {yt.get('version') or 'unknown'} ({yt.get('source')})"
            + (f"<br>{yt['path']}" if yt.get("path") else ""),
        )

    # ------------------------------------------------------------------ #
    # Button state
    # ------------------------------------------------------------------ #
    def _update_button_states(self) -> None:
        downloading = self._dl_thread is not None
        preparing = self._analyze_thread is not None
        self.btn_start.setEnabled(not downloading and not preparing)
        self.btn_start.setText("Downloading…" if downloading else "Download")
        self.btn_cancel.setVisible(downloading)
        self.btn_cancel.setEnabled(downloading)
        self.btn_paste.setEnabled(not downloading and not preparing)
        self.url_input.setEnabled(not downloading)

    def _collect_tasks(self) -> List[Dict[str, Any]]:
        analysis = self._analysis or {}
        kind = analysis.get("kind")
        video_task = {
            "url": analysis.get("url") or normalize_url(self.url_input.text()),
            "title": analysis.get("title", ""),
            "index": None,
        }
        if kind == KIND_VIDEO:
            return [video_task]
        if kind == KIND_VIDEO_IN_PLAYLIST and not self._download_playlist:
            return [video_task]
        if self._entries:
            tasks = self._checked_tasks()
            if tasks:
                return tasks
        return [video_task] if analysis.get("url") else []

    # ------------------------------------------------------------------ #
    # Download lifecycle
    # ------------------------------------------------------------------ #
    def _start_download(self, tasks: List[Dict[str, Any]]) -> None:
        if self._dl_thread is not None:
            return
        output_dir = self.output_edit_path()
        if not output_dir:
            QMessageBox.warning(self, "Download folder", "Please choose a download folder.")
            return
        try:
            ensure_dir(output_dir)
        except OSError as exc:
            QMessageBox.critical(self, "Download folder", f"Cannot create folder:\n{exc}")
            return
        if not is_writable_dir(output_dir):
            QMessageBox.critical(self, "Download folder", "The download folder is not writable.")
            return
        if not tasks:
            QMessageBox.information(self, "Nothing to download",
                                    "There are no downloadable videos in this link.")
            return

        quality = self._current_quality()
        if quality == QUALITY_AUDIO_MP3 and not self.ffmpeg.usable:
            QMessageBox.warning(self, "FFmpeg required",
                                "MP3 conversion requires FFmpeg, which was not detected.")
            return

        analysis = self._analysis or {}
        playlist_title = analysis.get("playlist_title") or analysis.get("title") or ""
        number_files = len(tasks) > 1

        payload = {
            "tasks": tasks,
            "output_dir": output_dir,
            "quality": quality,
            "make_playlist_folder": self.chk_playlist_folder.isChecked() and number_files,
            "playlist_title": playlist_title,
            "skip_existing": self.chk_skip_existing.isChecked(),
            "retries": int(self.settings.get("retries", 3)),
            "concurrent_fragments": int(self.settings.get("concurrent_fragments", 2)),
            "embed_metadata": bool(self.settings.get("embed_metadata", True)),
            "embed_thumbnail": bool(self.settings.get("embed_thumbnail", False)),
            "number_files": number_files,
            "ffmpeg_status": self.ffmpeg,
        }

        self._total_tasks = len(tasks)
        self._finished_tasks = 0
        self._counts = {"completed": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        self.bar_current.setValue(0)
        self.lbl_current.setText("Starting…")
        self.lbl_stats.setText("")
        self.progress_panel.setVisible(True)
        self._log("=" * 60)
        self._log(f"Starting download: {self._total_tasks} item(s), quality "
                  f"'{quality}', output '{output_dir}'")

        self._dl_thread = QThread(self)
        self._dl_worker = DownloadWorker(payload)
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.progress.connect(self._on_progress)
        self._dl_worker.postproc.connect(self._on_postproc)
        self._dl_worker.video_done.connect(self._on_video_done)
        self._dl_worker.finished.connect(self._on_download_finished)
        self._dl_worker.failed.connect(self._on_download_failed)
        self._dl_worker.finished.connect(self._dl_thread.quit)
        self._dl_worker.failed.connect(self._dl_thread.quit)
        self._dl_thread.finished.connect(self._cleanup_download)
        self._dl_thread.start()
        self._update_button_states()
        self.statusBar().showMessage("Downloading…")

    @Slot(dict)
    def _on_progress(self, payload: Dict[str, Any]) -> None:
        percent = payload.get("percent")
        if payload.get("title"):
            self.lbl_current.setText(payload["title"])
        if percent is not None:
            self.bar_current.setValue(int(max(0.0, min(1.0, percent)) * 1000))
        parts = []
        downloaded = payload.get("downloaded_bytes") or 0
        total = payload.get("total_bytes")
        if downloaded:
            parts.append(f"{human_bytes(downloaded)} / {human_bytes(total) if total else '?'}")
        if payload.get("speed"):
            parts.append(f"{human_bytes(payload['speed'])}/s")
        if payload.get("eta") is not None:
            parts.append(f"{human_duration(payload['eta'])} left")
        if self._total_tasks > 1:
            parts.insert(0, f"{self._finished_tasks + 1} of {self._total_tasks}")
        self.lbl_stats.setText("   •   ".join(parts))

    @Slot(dict)
    def _on_postproc(self, payload: Dict[str, Any]) -> None:
        pp = payload.get("postprocessor") or ""
        status = payload.get("status") or ""
        if pp and status:
            self.lbl_stats.setText(f"{status}: {pp}")

    @Slot(dict)
    def _on_video_done(self, result: Dict[str, Any]) -> None:
        status = result.get("status")
        if status in self._counts:
            self._counts[status] += 1
        self._finished_tasks += 1
        self._update_row_status(result)
        if self._total_tasks:
            self.bar_current.setValue(int(self._finished_tasks / self._total_tasks * 1000))

    def _update_row_status(self, result: Dict[str, Any]) -> None:
        index = result.get("index")
        title = result.get("title")
        for row, entry in enumerate(self._entries):
            match = (index is not None and entry.get("index") == index) or \
                    (title and entry.get("title") == title)
            if match:
                labels = {
                    STATUS_COMPLETED: ("Done", "#0f7a5a"),
                    STATUS_SKIPPED: ("Skipped", "#6b7a93"),
                    STATUS_FAILED: ("Failed", "#d92d20"),
                    "cancelled": ("Cancelled", "#b45309"),
                }
                text, color = labels.get(result.get("status"), ("—", "#333"))
                item = self.playlist_table.item(row, COL_STATUS)
                if item:
                    item.setText(text)
                    item.setForeground(QColor(color))
                break

    @Slot(dict)
    def _on_download_finished(self, summary: Dict[str, Any]) -> None:
        self._last_summary = summary
        completed = summary.get("completed", 0)
        skipped = summary.get("skipped", 0)
        failed = summary.get("failed", 0)
        cancelled = summary.get("cancelled", 0)
        elapsed = summary.get("elapsed", 0)

        parts = []
        if completed:
            parts.append(f"{completed} downloaded")
        if skipped:
            parts.append(f"{skipped} already existed")
        if failed:
            parts.append(f"{failed} failed")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        text = ", ".join(parts) or "Nothing downloaded"
        self._set_hint(f"{text}  •  {human_duration(elapsed)}", "HintError" if failed else "HintOk")
        self.lbl_current.setText("Finished")
        self.bar_current.setValue(1000)
        self.lbl_stats.setText("")
        self._log("-" * 60)
        self._log(f"Summary: {completed} completed, {skipped} skipped, "
                  f"{failed} failed, {cancelled} cancelled in {human_duration(elapsed)}.")
        for r in summary.get("results", []):
            if r.get("status") == STATUS_FAILED:
                self._log(f"  FAILED: {r.get('title')} — {r.get('error')}")
        self.statusBar().showMessage("Finished.")
        self._update_button_states()

        if failed and not cancelled:
            self._show_download_errors(summary)

    def _show_download_errors(self, summary: Dict[str, Any]) -> None:
        headline, message, hint = failure_report(summary)
        DetailsDialog(self, headline=headline, message=message, hint=hint).exec()

    @Slot(str)
    def _on_download_failed(self, message: str) -> None:
        self._log(f"Download job failed: {message}")
        self._set_hint(f"Download failed: {message}", "HintError")
        self.statusBar().showMessage("Failed.")
        self._update_button_states()
        DetailsDialog(
            self,
            headline="Download failed",
            message=message,
            hint="File ▸ Open logs folder keeps the full yt-dlp log.",
        ).exec()

    @Slot()
    def _cleanup_download(self) -> None:
        if self._dl_worker:
            self._dl_worker.deleteLater()
        if self._dl_thread:
            self._dl_thread.deleteLater()
        self._dl_worker = None
        self._dl_thread = None
        self._update_button_states()

    @Slot()
    def _on_cancel(self) -> None:
        if self._dl_worker is not None:
            self._log("Cancellation requested…")
            self.btn_cancel.setEnabled(False)
            self.statusBar().showMessage("Cancelling…")
            self._dl_worker.cancel()

    # ------------------------------------------------------------------ #
    # Logging / shutdown
    # ------------------------------------------------------------------ #
    @Slot(str)
    def _log(self, message: str) -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._dl_thread is not None:
            reply = QMessageBox.question(
                self, "Download in progress",
                "A download is running. Cancel it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._dl_worker:
                self._dl_worker.cancel()
            if self._dl_thread is not None:
                self._dl_thread.quit()
                self._dl_thread.wait(15000)
        if self._analyze_thread is not None:
            self._analyze_thread.quit()
            self._analyze_thread.wait(5000)
        if self._ytdlp_thread is not None:
            # The update may be mid-download; it writes only to its own folder,
            # so leaving it to finish would block closing the window.
            self._ytdlp_thread.quit()
            self._ytdlp_thread.wait(3000)
        self._save_settings()
        event.accept()
