"""PySide6 main window for the YouTube Downloader.

The window uses a compact card layout: a header, a *Link* card, a *Download*
card (mode, quality, output), an optional *Videos* card for playlists, and an
optional *Progress* card shown while downloading. Content lives in a scroll
area with a sticky action bar at the bottom. All network work happens in worker
threads; every widget update here runs on the GUI thread because it is triggered
from Qt signals.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
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
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .downloader import (
    KIND_PLAYLIST,
    KIND_VIDEO,
    KIND_VIDEO_IN_PLAYLIST,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from .format_service import (
    MAX_PLAYLIST_RESOLUTION_NOTE,
    QUALITY_AUDIO,
    QUALITY_AUDIO_MP3,
    QUALITY_BEST,
    available_quality_options,
    default_quality_options,
    list_summaries,
    resolve_format,
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
    sanitize_filename,
    validate_url,
)
from .workers import AnalyzeWorker, DownloadWorker

MODE_VIDEO = "video"
MODE_PLAYLIST = "playlist"
MODE_SELECTED = "selected"

COL_CHECK, COL_INDEX, COL_TITLE, COL_DURATION, COL_AVAIL, COL_STATUS = range(6)


STYLESHEET = """
QWidget {
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 10pt;
    color: #1f2937;
}
#Root { background: #f4f5f7; }
#TopBar { background: #ffffff; border-bottom: 1px solid #eceef2; }
#ActionBar { background: #ffffff; border-top: 1px solid #eceef2; }
#Scroll, #Scroll > QWidget > QWidget { background: transparent; border: 0; }
#AppTitle { font-size: 15pt; font-weight: 700; color: #0f172a; }
#AppSubtitle { color: #8a94a6; font-size: 9pt; }

QFrame#Card { background: #ffffff; border: 1px solid #eceef2; border-radius: 14px; }
QLabel#CardTitle { font-size: 10.5pt; font-weight: 700; color: #0f172a; }
QLabel#Field { color: #5b6472; font-weight: 600; }
QLabel#Hint { color: #8a94a6; }
QLabel#Current { font-weight: 600; color: #0f172a; }
QLabel#EmptyState { color: #9aa3b2; }
QLabel#EmptyTitle { font-size: 13pt; font-weight: 700; color: #6b7280; }
QLabel#Pill {
    background: #eef2ff; color: #4338ca; border-radius: 9px;
    padding: 2px 9px; font-size: 8.5pt; font-weight: 600;
}
#Banner { border-radius: 10px; padding: 10px 12px; }

QLineEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 10px;
    min-height: 20px;
    selection-background-color: #bfdbfe;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #2563eb; }
QLineEdit:disabled, QComboBox:disabled { background: #f9fafb; color: #9ca3af; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background: #ffffff;
    selection-background-color: #eff6ff;
    selection-color: #1d4ed8;
    padding: 4px;
    outline: 0;
}

QPushButton {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 14px;
    color: #374151;
}
QPushButton:hover { background: #f9fafb; }
QPushButton:pressed { background: #f3f4f6; }
QPushButton:disabled { color: #9ca3af; border-color: #eceef2; background: #f9fafb; }
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b82f6, stop:1 #2563eb);
    color: #ffffff;
    border: 0;
    border-radius: 10px;
    font-weight: 700;
    padding: 11px 20px;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2f74ee, stop:1 #1d4ed8);
}
QPushButton#Primary:pressed { background: #1e40af; }
QPushButton#Primary:disabled { background: #b7ccf6; color: #ffffff; }
QPushButton#Flat {
    border: 0;
    background: transparent;
    color: #374151;
    padding: 7px 10px;
}
QPushButton#Flat:hover { background: #f3f4f6; border-radius: 8px; }
QPushButton#Link {
    border: 0;
    background: transparent;
    color: #2563eb;
    padding: 5px 6px;
}
QPushButton#Link:hover { color: #1d4ed8; }
QPushButton#Seg {
    background: #f3f4f6;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 8px 16px;
    color: #4b5563;
    font-weight: 600;
}
QPushButton#Seg:hover { background: #e9ebef; }
QPushButton#Seg:checked { background: #2563eb; color: #ffffff; border-color: #2563eb; }
QPushButton#Seg:checked:hover { background: #1d4ed8; }
QPushButton#Seg:disabled { color: #b6bcc7; background: #f6f7f9; }
QPushButton#Icon {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 12px;
}
QPushButton#Icon:hover { background: #f9fafb; }

QProgressBar {
    border: 0;
    background: #edeff3;
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #2563eb);
    border-radius: 7px;
}

QTableWidget {
    background: #ffffff;
    border: 0;
    gridline-color: transparent;
    selection-background-color: #eef4ff;
    selection-color: #0f172a;
    outline: 0;
}
QTableWidget::item { padding: 4px 6px; border-bottom: 1px solid #f4f5f7; }
QTableWidget::item:hover { background: #f8fafc; }
QHeaderView::section {
    background: #fafbfc;
    border: 0;
    border-bottom: 1px solid #eceef2;
    padding: 8px 6px;
    color: #8a94a6;
    font-weight: 700;
    font-size: 8.5pt;
}

QCheckBox { color: #374151; spacing: 9px; }

QPlainTextEdit {
    background: #0f172a;
    color: #cbd5e1;
    border: 0;
    border-radius: 10px;
    padding: 10px;
    selection-background-color: #334155;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 9pt;
}

QStatusBar { background: #ffffff; border-top: 1px solid #eceef2; color: #8a94a6; }
QStatusBar::item { border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #d1d5db; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #b9bec7; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""



class _CheckStyle(QProxyStyle):
    """Draws a crisp, modern checkbox indicator (blue box with a white tick)."""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            self._draw_check(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_check(option, painter) -> None:
        rect = option.rect
        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        if not enabled:
            painter.setPen(QPen(QColor("#e2e5ea")))
            painter.setBrush(QColor("#f1f3f5"))
        elif checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563eb"))
        else:
            painter.setPen(QPen(QColor("#cbd5e1")))
            painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(box, 5, 5)
        if checked:
            pen = QPen(QColor("#ffffff") if enabled else QColor("#b6bcc7"))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            painter.drawLine(int(x + w * 0.26), int(y + h * 0.52),
                             int(x + w * 0.44), int(y + h * 0.70))
            painter.drawLine(int(x + w * 0.44), int(y + h * 0.70),
                             int(x + w * 0.76), int(y + h * 0.32))
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None, ffmpeg: Optional[FFmpegStatus] = None):
        super().__init__()
        app = QApplication.instance()
        if app is not None and not isinstance(app.style(), _CheckStyle):
            app.setStyle(_CheckStyle(app.style()))
        self.settings = settings or Settings()
        self.ffmpeg: FFmpegStatus = ffmpeg or detect_ffmpeg(
            self.settings.get("ffmpeg_location") or None
        )

        self._analysis: Optional[Dict[str, Any]] = None
        self._entries: List[Dict[str, Any]] = []
        self._formats: List[Dict[str, Any]] = []
        self._last_summary: Optional[Dict[str, Any]] = None
        self._format_cache: Dict[str, List[Dict[str, Any]]] = {}

        self._analyze_thread: Optional[QThread] = None
        self._analyze_worker: Optional[AnalyzeWorker] = None
        self._dl_thread: Optional[QThread] = None
        self._dl_worker: Optional[DownloadWorker] = None
        self._fmt_thread: Optional[QThread] = None
        self._fmt_worker: Optional[AnalyzeWorker] = None

        self._total_tasks = 0
        self._finished_tasks = 0
        self._counts = {"completed": 0, "skipped": 0, "failed": 0, "cancelled": 0}

        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(QIcon(self._make_logo_pixmap(64)))
        self.resize(980, 720)
        self.setMinimumSize(780, 560)

        self._build_menu()
        self._build_ui()
        self._apply_settings()
        self._check_ffmpeg(initial=True)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

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

        help_menu = menubar.addMenu("&Help")
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
        grad.setColorAt(0.0, QColor("#3b82f6"))
        grad.setColorAt(1.0, QColor("#1d4ed8"))
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

    def _make_card(self):
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(15, 23, 42, 26))
        card.setGraphicsEffect(shadow)
        return card, lay

    def _make_seg(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("Seg")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    @staticmethod
    def _card_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("CardTitle")
        return lbl

    def _build_ui(self) -> None:
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header --------------------------------------------------- #
        topbar = QFrame()
        topbar.setObjectName("TopBar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(24, 14, 24, 14)
        tb.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(self._make_logo_pixmap(42))
        logo.setFixedSize(42, 42)
        tb.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        app_title = QLabel(APP_NAME)
        app_title.setObjectName("AppTitle")
        subtitle = QLabel("Paste a link, choose a quality, download.")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(app_title)
        titles.addWidget(subtitle)
        tb.addLayout(titles)
        tb.addStretch(1)
        root.addWidget(topbar)

        # ---- Content -------------------------------------------------- #
        content = QWidget()
        content.setObjectName("Content")
        col = QVBoxLayout(content)
        col.setContentsMargins(26, 20, 26, 26)
        col.setSpacing(16)

        self.ffmpeg_banner = QLabel()
        self.ffmpeg_banner.setObjectName("Banner")
        self.ffmpeg_banner.setWordWrap(True)
        self.ffmpeg_banner.setVisible(False)
        col.addWidget(self.ffmpeg_banner)

        # ---- Link card ------------------------------------------------ #
        link_card, link_lay = self._make_card()
        link_lay.addWidget(self._card_title("Link"))
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.youtube.com/watch?v=…  or a playlist link")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self._on_analyze)
        self.url_input.textChanged.connect(self._on_url_changed)
        self.btn_paste = QPushButton("Paste")
        self.btn_paste.setObjectName("Flat")
        self.btn_analyze = QPushButton("Analyze")
        self.btn_analyze.setObjectName("Primary")
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("Flat")
        self.btn_paste.clicked.connect(self._on_paste)
        self.btn_analyze.clicked.connect(self._on_analyze)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_analyze.setDefault(True)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.btn_paste)
        url_row.addWidget(self.btn_analyze)
        url_row.addWidget(self.btn_clear)
        link_lay.addLayout(url_row)
        self.url_status = QLabel("Enter a URL and click Analyze.")
        self.url_status.setObjectName("Hint")
        self.url_status.setWordWrap(True)
        link_lay.addWidget(self.url_status)
        col.addWidget(link_card)

        # ---- Download card (mode + quality + output) ------------------ #
        opt_card, opt_lay = self._make_card()
        opt_lay.addWidget(self._card_title("Download"))

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.radio_video = self._make_seg("This video")
        self.radio_playlist = self._make_seg("Whole playlist")
        self.radio_selected = self._make_seg("Selected videos")
        for rb in (self.radio_video, self.radio_playlist, self.radio_selected):
            self.mode_group.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        self.radio_video.setChecked(True)
        self.radio_video.toggled.connect(self._on_mode_changed)
        self.radio_playlist.toggled.connect(self._on_mode_changed)
        self.radio_selected.toggled.connect(self._on_mode_changed)
        opt_lay.addLayout(mode_row)

        q_row = QHBoxLayout()
        q_row.setSpacing(10)
        q_lbl = QLabel("Quality")
        q_lbl.setObjectName("Field")
        self.quality_combo = QComboBox()
        self.quality_combo.setMinimumWidth(240)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.btn_formats = QPushButton("Show formats")
        self.btn_formats.setObjectName("Link")
        self.btn_formats.setCheckable(True)
        self.btn_formats.setVisible(False)
        self.btn_formats.toggled.connect(self._on_toggle_formats)
        q_row.addWidget(q_lbl)
        q_row.addWidget(self.quality_combo)
        q_row.addStretch(1)
        q_row.addWidget(self.btn_formats)
        opt_lay.addLayout(q_row)

        self.quality_note = QLabel("")
        self.quality_note.setObjectName("Hint")
        self.quality_note.setWordWrap(True)
        opt_lay.addWidget(self.quality_note)

        self.formats_container = QWidget()
        fc = QVBoxLayout(self.formats_container)
        fc.setContentsMargins(0, 0, 0, 0)
        self.format_table = QTableWidget(0, 5)
        self.format_table.setHorizontalHeaderLabels(
            ["ID", "Resolution", "FPS", "Video", "Audio"]
        )
        fhdr = self.format_table.horizontalHeader()
        fhdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.format_table.verticalHeader().setVisible(False)
        self.format_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.format_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.format_table.setMinimumHeight(180)
        fc.addWidget(self.format_table)
        self.formats_container.setVisible(False)
        opt_lay.addWidget(self.formats_container)

        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        o_lbl = QLabel("Save to")
        o_lbl.setObjectName("Field")
        self.output_edit = QLineEdit()
        self.btn_browse = QPushButton("Change")
        self.btn_browse.setObjectName("Flat")
        self.btn_browse.clicked.connect(self._on_browse)
        out_row.addWidget(o_lbl)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.btn_browse)
        opt_lay.addLayout(out_row)

        chk_row = QHBoxLayout()
        chk_row.setSpacing(20)
        self.chk_playlist_folder = QCheckBox("Separate folder per playlist")
        self.chk_skip_existing = QCheckBox("Skip files that already exist")
        chk_row.addWidget(self.chk_playlist_folder)
        chk_row.addWidget(self.chk_skip_existing)
        chk_row.addStretch(1)
        opt_lay.addLayout(chk_row)
        col.addWidget(opt_card)

        # ---- Empty state (before anything is analyzed) ---------------- #
        self.empty_state = QWidget()
        es = QVBoxLayout(self.empty_state)
        es.setContentsMargins(0, 0, 0, 0)
        es.setSpacing(6)
        es.addStretch(1)
        es_icon = QLabel()
        es_icon.setPixmap(self._make_logo_pixmap(56))
        es_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_title = QLabel("Nothing loaded yet")
        es_title.setObjectName("EmptyTitle")
        es_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_sub = QLabel("Paste a video or playlist link above, then press Analyze.")
        es_sub.setObjectName("EmptyState")
        es_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_sub.setWordWrap(True)
        es.addWidget(es_icon)
        es.addWidget(es_title)
        es.addWidget(es_sub)
        es.addStretch(1)
        col.addWidget(self.empty_state, 1)

        # ---- Playlist card (only for playlists) ----------------------- #
        self.playlist_card, pl_lay = self._make_card()
        pl_head = QHBoxLayout()
        pl_head.setSpacing(8)
        pl_head.addWidget(self._card_title("Videos"))
        self.playlist_count_pill = QLabel("")
        self.playlist_count_pill.setObjectName("Pill")
        self.playlist_count_pill.setVisible(False)
        pl_head.addWidget(self.playlist_count_pill)
        pl_head.addStretch(1)
        self.btn_select_all = QPushButton("All")
        self.btn_deselect_all = QPushButton("None")
        self.btn_download_selected = QPushButton("Download selected")
        for b in (self.btn_select_all, self.btn_deselect_all, self.btn_download_selected):
            b.setObjectName("Flat")
            pl_head.addWidget(b)
        self.btn_select_all.clicked.connect(lambda: self._set_all_checks(True))
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_checks(False))
        self.btn_download_selected.clicked.connect(self._on_download_selected)
        pl_lay.addLayout(pl_head)

        self.playlist_table = QTableWidget(0, 6)
        self.playlist_table.setHorizontalHeaderLabels(
            ["", "#", "Title", "Duration", "Available", "Status"]
        )
        hdr = self.playlist_table.horizontalHeader()
        hdr.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.ResizeToContents)
        for c in (COL_INDEX, COL_DURATION, COL_AVAIL, COL_STATUS):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.playlist_table.verticalHeader().setVisible(False)
        self.playlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.playlist_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.playlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.playlist_table.itemSelectionChanged.connect(self._on_playlist_selection)
        self.playlist_table.setMinimumHeight(200)
        pl_lay.addWidget(self.playlist_table)
        self.playlist_card.setVisible(False)
        col.addWidget(self.playlist_card, 1)

        # ---- Progress card (only while/after downloading) ------------- #
        self.progress_card, pr_lay = self._make_card()
        pr_head = QHBoxLayout()
        self.lbl_current = QLabel("Ready")
        self.lbl_current.setObjectName("Current")
        self.btn_details = QPushButton("Details")
        self.btn_details.setObjectName("Link")
        self.btn_details.setCheckable(True)
        self.btn_details.toggled.connect(self._on_toggle_details)
        pr_head.addWidget(self.lbl_current, 1)
        pr_head.addWidget(self.btn_details)
        pr_lay.addLayout(pr_head)

        self.bar_current = QProgressBar()
        self.bar_current.setRange(0, 1000)
        self.bar_current.setValue(0)
        pr_lay.addWidget(self.bar_current)

        overall_row = QHBoxLayout()
        overall_row.setSpacing(10)
        self.lbl_overall = QLabel("")
        self.lbl_overall.setObjectName("Hint")
        self.bar_overall = QProgressBar()
        self.bar_overall.setRange(0, 1000)
        self.bar_overall.setValue(0)
        overall_row.addWidget(self.lbl_overall)
        overall_row.addWidget(self.bar_overall, 1)
        pr_lay.addLayout(overall_row)

        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("Hint")
        self.lbl_counts = QLabel("")
        self.lbl_counts.setObjectName("Hint")
        pr_lay.addWidget(self.lbl_stats)
        pr_lay.addWidget(self.lbl_counts)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setMinimumHeight(150)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.log_view.setFont(mono)
        self.log_view.setVisible(False)
        pr_lay.addWidget(self.log_view)

        self.progress_card.setVisible(False)
        col.addWidget(self.progress_card)

        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.scroll = scroll
        root.addWidget(scroll, 1)

        # ---- Action bar (sticky at the bottom) ------------------------ #
        action_bar = QFrame()
        action_bar.setObjectName("ActionBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(22, 12, 22, 12)
        actions.setSpacing(8)
        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("Flat")
        self.btn_retry = QPushButton("Retry failed")
        self.btn_retry.setObjectName("Flat")
        self.btn_retry.setVisible(False)
        self.btn_open.clicked.connect(self._open_output_folder)
        self.btn_retry.clicked.connect(self._on_retry)
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_retry)
        actions.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("Flat")
        self.btn_cancel.setVisible(False)
        self.btn_start = QPushButton("Download")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.setMinimumWidth(170)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_start.clicked.connect(self._on_start)
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_start)
        root.addWidget(action_bar)

        # ---- Status bar ----------------------------------------------- #
        self.statusBar().showMessage("Ready")
        self.ffmpeg_indicator = QLabel()
        self.statusBar().addPermanentWidget(self.ffmpeg_indicator)

        # Nicer pointing-hand cursor on every interactive button.
        for b in (self.btn_analyze, self.btn_paste, self.btn_clear, self.btn_browse,
                  self.btn_start, self.btn_cancel, self.btn_retry, self.btn_open,
                  self.btn_select_all, self.btn_deselect_all, self.btn_download_selected,
                  self.btn_formats, self.btn_details):
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        self._set_playlist_enabled(False)
        self._update_button_states()

    # ------------------------------------------------------------------ #
    # Small UI toggles
    # ------------------------------------------------------------------ #
    @Slot(bool)
    def _on_toggle_formats(self, checked: bool) -> None:
        self.formats_container.setVisible(checked)
        self.btn_formats.setText("Hide formats" if checked else "Show formats")

    @Slot(bool)
    def _on_toggle_details(self, checked: bool) -> None:
        self.log_view.setVisible(checked)
        self.btn_details.setText("Hide details" if checked else "Details")
        if checked:
            self._scroll_to_bottom()

    def _scroll_to_top(self) -> None:
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def _scroll_to_bottom(self) -> None:
        sb = self.scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #
    def _apply_settings(self) -> None:
        self.output_edit.setText(self.settings.get("output_dir"))
        self.chk_playlist_folder.setChecked(bool(self.settings.get("make_playlist_folder", True)))
        self.chk_skip_existing.setChecked(bool(self.settings.get("skip_existing", True)))
        mode = self.settings.get("download_mode", MODE_VIDEO)
        {
            MODE_VIDEO: self.radio_video,
            MODE_PLAYLIST: self.radio_playlist,
            MODE_SELECTED: self.radio_selected,
        }.get(mode, self.radio_video).setChecked(True)

        self._reload_quality_combo(default_quality_options(self.ffmpeg.available),
                                   self.settings.get("quality", QUALITY_BEST))

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
            "output_dir": self.output_edit.text().strip(),
            "quality": self.quality_combo.currentData() or QUALITY_BEST,
            "download_mode": self._current_mode(),
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
        if st.available:
            # No banner when everything is fine — keep the UI calm.
            self.ffmpeg_banner.setVisible(False)
            if getattr(self, "ffmpeg_indicator", None) is not None:
                self.ffmpeg_indicator.setText("● FFmpeg ready")
                self.ffmpeg_indicator.setStyleSheet("color:#15803d;")
                self.ffmpeg_indicator.setToolTip(st.describe())
        else:
            self.ffmpeg_banner.setText(
                "FFmpeg was not found. High-resolution downloads (separate video + "
                "audio streams) and MP3 conversion are unavailable, so only single-file "
                "formats can be used. Install it with  winget install Gyan.FFmpeg  or set "
                "its location from the menu (File ▸ Set FFmpeg location…)."
            )
            self.ffmpeg_banner.setStyleSheet(
                "background:#fff7ed; color:#9a3412; border:1px solid #fed7aa;"
            )
            self.ffmpeg_banner.setVisible(True)
            if getattr(self, "ffmpeg_indicator", None) is not None:
                self.ffmpeg_indicator.setText("● FFmpeg missing")
                self.ffmpeg_indicator.setStyleSheet("color:#b91c1c;")
                self.ffmpeg_indicator.setToolTip("FFmpeg / FFprobe not found.")
        if not initial:
            self._reload_quality_combo(
                default_quality_options(st.available),
                self.quality_combo.currentData() or QUALITY_BEST,
            )

    def _choose_ffmpeg_location(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select the folder containing ffmpeg/ffprobe")
        if not path:
            return
        self.settings.set("ffmpeg_location", path)
        self.settings.save()
        self.ffmpeg = detect_ffmpeg(path)
        self._check_ffmpeg()
        self._log(f"FFmpeg re-detected: {self.ffmpeg.describe()}")

    # ------------------------------------------------------------------ #
    # URL / analysis
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_paste(self) -> None:
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text().strip()
        if text:
            self.url_input.setText(text)
        else:
            self._log("Clipboard is empty.")

    @Slot(str)
    def _on_url_changed(self, text: str) -> None:
        ok, err = validate_url(text)
        if not text.strip():
            self.url_status.setText("Enter a URL and click Analyze.")
            self.url_status.setStyleSheet("")
        elif ok:
            kind = classify_url(text)
            label = {
                KIND_VIDEO: "Detected: single video.",
                KIND_PLAYLIST: "Detected: playlist.",
                KIND_VIDEO_IN_PLAYLIST: "Detected: video inside a playlist (choose a mode).",
            }.get(kind, "Detected.")
            self.url_status.setText(f"{label} Click Analyze URL to load metadata.")
            self.url_status.setStyleSheet("color:#1b5e20;")
        else:
            self.url_status.setText(err or "Invalid URL.")
            self.url_status.setStyleSheet("color:#8a1c1c;")

    @Slot()
    def _on_clear(self) -> None:
        self.url_input.clear()
        self.playlist_table.setRowCount(0)
        self.format_table.setRowCount(0)
        self._entries = []
        self._formats = []
        self._analysis = None
        self._format_cache.clear()
        self.url_status.setText("Enter a URL and click Analyze.")
        self.url_status.setStyleSheet("")
        self.playlist_card.setVisible(False)
        self.progress_card.setVisible(False)
        self.formats_container.setVisible(False)
        self.empty_state.setVisible(True)
        self._scroll_to_top()
        self.btn_formats.setVisible(False)
        self.btn_formats.setChecked(False)
        self.btn_details.setChecked(False)
        self.radio_video.setEnabled(True)
        self.radio_video.setChecked(True)
        self._set_playlist_enabled(False)
        self._reload_quality_combo(default_quality_options(self.ffmpeg.available), QUALITY_BEST)
        self._update_button_states()

    @Slot()
    def _on_analyze(self) -> None:
        if self._analyze_thread is not None or self._dl_thread is not None:
            return
        url = normalize_url(self.url_input.text())
        ok, err = validate_url(url)
        if not ok:
            QMessageBox.warning(self, "Invalid URL", err or "Invalid URL.")
            return

        self._set_busy_analyze(True)
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
        self.empty_state.setVisible(False)
        self._scroll_to_top()
        kind = payload.get("kind")
        self._formats = payload.get("formats") or []

        if kind == KIND_VIDEO:
            title = payload.get("title") or "(unknown)"
            self.url_status.setText(f"Video: {title}  •  {human_duration(payload.get('duration'))}")
            self.url_status.setStyleSheet("color:#1b5e20;")
            self._set_playlist_enabled(False)
            self.playlist_card.setVisible(False)
            self.radio_video.setChecked(True)
            self.radio_playlist.setEnabled(False)
            self.radio_selected.setEnabled(False)
        else:
            entries = payload.get("entries") or []
            pl_title = payload.get("playlist_title") or payload.get("title") or "(playlist)"
            self.url_status.setText(
                f"Playlist: {pl_title}  •  {len(entries)} entries"
                + (f"  •  video: {payload.get('title')}" if payload.get("title") and kind == KIND_VIDEO_IN_PLAYLIST else "")
            )
            self.url_status.setStyleSheet("color:#1b5e20;")
            self._populate_playlist(entries)
            self.playlist_count_pill.setText(f"{len(entries)} videos")
            self.playlist_count_pill.setVisible(True)
            self.playlist_card.setVisible(True)
            self._set_playlist_enabled(True)
            if kind == KIND_VIDEO_IN_PLAYLIST:
                video_ok = not payload.get("video_error")
                self.radio_video.setEnabled(video_ok)
                self.radio_playlist.setEnabled(True)
                self.radio_selected.setEnabled(True)
                if video_ok:
                    # Safer default when both are present.
                    self.radio_video.setChecked(True)
                    self._log("URL contains both a video and a playlist. "
                              "Defaulting to 'Single video only' — change the mode to download the playlist.")
                else:
                    self.radio_playlist.setChecked(True)
                    self._log("The linked video could not be resolved: "
                              f"{payload.get('video_error')} — playlist modes enabled instead.")
            else:
                self.radio_playlist.setEnabled(True)
                self.radio_selected.setEnabled(True)
                self.radio_video.setEnabled(False)
                self.radio_playlist.setChecked(True)

        # Quality options.
        if self._formats:
            self._reload_quality_combo(
                available_quality_options(self._formats, self.ffmpeg.available), QUALITY_BEST
            )
            self._populate_format_table(self._formats)
        else:
            self._reload_quality_combo(default_quality_options(self.ffmpeg.available), QUALITY_BEST)
            self.format_table.setRowCount(0)
        self.btn_formats.setVisible(bool(self._formats))
        self.btn_formats.setChecked(False)
        self.formats_container.setVisible(False)

        self._on_mode_changed()
        self._on_quality_changed()
        self._set_busy_analyze(False)
        self._log("Analysis complete.")

    @Slot(str)
    def _on_analysis_failed(self, message: str) -> None:
        self._set_busy_analyze(False)
        self.url_status.setText(f"Analysis failed: {message}")
        self.url_status.setStyleSheet("color:#8a1c1c;")
        self._log(f"Analysis failed: {message}")
        QMessageBox.critical(self, "Analysis failed", message)

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
    # Playlist table
    # ------------------------------------------------------------------ #
    def _populate_playlist(self, entries: List[Dict[str, Any]]) -> None:
        self._entries = list(entries)
        self.playlist_table.setRowCount(0)
        self.playlist_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            available = bool(entry.get("available", True))
            chk.setCheckState(
                Qt.CheckState.Checked if available else Qt.CheckState.Unchecked
            )
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

            avail = QTableWidgetItem("Yes" if available else "No")
            avail.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not available:
                avail.setForeground(QColor("#8a1c1c"))
                avail.setToolTip(entry.get("reason", ""))
            self.playlist_table.setItem(row, COL_AVAIL, avail)

            status = QTableWidgetItem("—")
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.playlist_table.setItem(row, COL_STATUS, status)

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

    @Slot()
    def _on_playlist_selection(self) -> None:
        rows = self.playlist_table.selectionModel().selectedRows() if self.playlist_table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        if row >= len(self._entries):
            return
        entry = self._entries[row]
        if not entry.get("available", True):
            return
        url = entry["url"]
        if not url:
            return
        cached = self._format_cache.get(url)
        if cached is not None:
            self._formats = cached
            self._populate_format_table(cached)
            self._reload_quality_combo(available_quality_options(cached, self.ffmpeg.available),
                                       self.quality_combo.currentData() or QUALITY_BEST)
            self._on_quality_changed()
            return
        self._fetch_formats(url, row)

    def _fetch_formats(self, url: str, row: int) -> None:
        if self._fmt_thread is not None:
            return
        self._log(f"Fetching formats for entry {row + 1}…")
        self._fmt_thread = QThread(self)
        self._fmt_worker = AnalyzeWorker(url, ffmpeg_status=self.ffmpeg)

        def on_ok(payload: Dict[str, Any]) -> None:
            formats = payload.get("formats") or []
            self._format_cache[url] = formats
            self._formats = formats
            self._populate_format_table(formats)
            self._reload_quality_combo(available_quality_options(formats, self.ffmpeg.available),
                                       self.quality_combo.currentData() or QUALITY_BEST)
            self._on_quality_changed()

        def on_fail(msg: str) -> None:
            self._log(f"Could not fetch formats for entry {row + 1}: {msg}")

        self._fmt_worker.moveToThread(self._fmt_thread)
        self._fmt_thread.started.connect(self._fmt_worker.run)
        self._fmt_worker.log.connect(self._log)
        self._fmt_worker.finished.connect(on_ok)
        self._fmt_worker.failed.connect(on_fail)
        self._fmt_worker.finished.connect(self._fmt_thread.quit)
        self._fmt_worker.failed.connect(self._fmt_thread.quit)
        self._fmt_thread.finished.connect(self._cleanup_fmt)
        self._fmt_thread.start()

    @Slot()
    def _cleanup_fmt(self) -> None:
        if self._fmt_worker:
            self._fmt_worker.deleteLater()
        if self._fmt_thread:
            self._fmt_thread.deleteLater()
        self._fmt_worker = None
        self._fmt_thread = None

    def _populate_format_table(self, formats: List[Dict[str, Any]]) -> None:
        summaries = list_summaries(formats)
        # Show video/audio-bearing formats, largest resolution first.
        summaries.sort(key=lambda s: (s["height"] or 0, s["tbr"] or 0), reverse=True)
        self.format_table.setRowCount(len(summaries))
        for row, s in enumerate(summaries):
            self.format_table.setItem(row, 0, QTableWidgetItem(s["format_id"]))
            self.format_table.setItem(row, 1, QTableWidgetItem(s["resolution"]))
            self.format_table.setItem(row, 2, QTableWidgetItem(str(s["fps"] or "—")))
            self.format_table.setItem(row, 3, QTableWidgetItem(s["vcodec"] or "—"))
            self.format_table.setItem(row, 4, QTableWidgetItem(s["acodec"] or "—"))

    # ------------------------------------------------------------------ #
    # Mode / quality
    # ------------------------------------------------------------------ #
    def _current_mode(self) -> str:
        if self.radio_playlist.isChecked():
            return MODE_PLAYLIST
        if self.radio_selected.isChecked():
            return MODE_SELECTED
        return MODE_VIDEO

    @Slot()
    def _on_mode_changed(self) -> None:
        mode = self._current_mode()
        # Quality options depend on whether we have concrete formats.
        if mode == MODE_VIDEO and self._formats:
            self._reload_quality_combo(
                available_quality_options(self._formats, self.ffmpeg.available),
                self.quality_combo.currentData() or QUALITY_BEST,
            )
        elif mode in (MODE_PLAYLIST, MODE_SELECTED):
            self._reload_quality_combo(
                default_quality_options(self.ffmpeg.available),
                self.quality_combo.currentData() or QUALITY_BEST,
            )
        self._update_button_states()

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
        quality = self.quality_combo.currentData() or QUALITY_BEST
        mode = self._current_mode()
        if mode == MODE_VIDEO and self._formats:
            resolved = resolve_format(quality, self._formats, self.ffmpeg.available)
            text = f"Will download: {resolved.describe()}"
            if resolved.note:
                text += f"\nNote: {resolved.note}"
            color = "#8a1c1c" if resolved.fallback else "#333"
            self.quality_note.setText(text)
            self.quality_note.setStyleSheet(f"color:{color};")
        elif mode in (MODE_PLAYLIST, MODE_SELECTED):
            if quality == QUALITY_AUDIO_MP3 and not self.ffmpeg.available:
                self.quality_note.setText("MP3 conversion needs FFmpeg (not detected).")
                self.quality_note.setStyleSheet("color:#8a1c1c;")
            else:
                self.quality_note.setText(MAX_PLAYLIST_RESOLUTION_NOTE)
                self.quality_note.setStyleSheet("color:#555;")
        else:
            self.quality_note.setText("Analyze a video to see the exact format that will be used.")
            self.quality_note.setStyleSheet("color:#555;")

    # ------------------------------------------------------------------ #
    # Output / actions
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_browse(self) -> None:
        start = self.output_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if path:
            self.output_edit.setText(path)

    @Slot()
    def _open_output_folder(self) -> None:
        path = self.output_edit.text().strip()
        if not path:
            QMessageBox.information(self, "No folder", "Choose an output folder first.")
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
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "A PySide6 + yt-dlp desktop downloader for YouTube videos and playlists.<br>"
            "Download only content you are authorised to download.<br><br>"
            f"<b>FFmpeg:</b> {self.ffmpeg.describe().replace(chr(10), '<br>')}",
        )

    def _set_playlist_enabled(self, enabled: bool) -> None:
        for w in (self.btn_select_all, self.btn_deselect_all, self.btn_download_selected):
            w.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Busy state helpers
    # ------------------------------------------------------------------ #
    def _set_busy_analyze(self, busy: bool) -> None:
        self.btn_analyze.setEnabled(not busy)
        self.btn_analyze.setText("Analyzing…" if busy else "Analyze")
        self.btn_paste.setEnabled(not busy)
        self._update_button_states()

    def _update_button_states(self) -> None:
        downloading = self._dl_thread is not None
        analyzing = self._analyze_thread is not None
        self.btn_start.setEnabled(not downloading and not analyzing)
        self.btn_start.setText("Downloading…" if downloading else "Download")
        self.btn_cancel.setVisible(downloading)
        self.btn_cancel.setEnabled(downloading)
        has_failed = (
            not downloading and self._last_summary is not None
            and self._last_summary.get("failed", 0) > 0
        )
        self.btn_retry.setVisible(has_failed)
        self.btn_retry.setEnabled(has_failed)
        self.btn_download_selected.setEnabled(
            not downloading and bool(self._entries)
        )

    def _collect_tasks(self) -> List[Dict[str, Any]]:
        mode = self._current_mode()
        analysis = self._analysis or {}
        if mode == MODE_VIDEO:
            url = analysis.get("url") or normalize_url(self.url_input.text())
            return [{"url": url, "title": analysis.get("title", ""), "index": None}]
        if mode == MODE_PLAYLIST:
            return [
                {"url": e["url"], "title": e.get("title", ""), "index": e.get("index")}
                for e in self._entries if e.get("available", True) and e.get("url")
            ]
        return self._checked_tasks()

    # ------------------------------------------------------------------ #
    # Download lifecycle
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_download_selected(self) -> None:
        if not self._entries:
            QMessageBox.information(self, "No playlist", "Analyze a playlist first.")
            return
        if not self._checked_tasks():
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one available video in the list.")
            return
        self.radio_selected.setChecked(True)
        self._start_download(self._collect_tasks())

    @Slot()
    def _on_start(self) -> None:
        self._start_download(self._collect_tasks())

    def _start_download(self, tasks: List[Dict[str, Any]]) -> None:
        if self._dl_thread is not None:
            return
        output_dir = self.output_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "Output folder", "Please choose an output folder.")
            return
        try:
            ensure_dir(output_dir)
        except OSError as exc:
            QMessageBox.critical(self, "Output folder", f"Cannot create output folder:\n{exc}")
            return
        if not is_writable_dir(output_dir):
            QMessageBox.critical(self, "Output folder", "The output folder is not writable.")
            return

        if not tasks:
            QMessageBox.information(self, "No videos", "There are no downloadable videos for the current selection.")
            return

        quality = self.quality_combo.currentData() or QUALITY_BEST
        if quality == QUALITY_AUDIO_MP3 and not self.ffmpeg.available:
            QMessageBox.warning(self, "FFmpeg required",
                                "MP3 conversion requires FFmpeg, which was not detected. "
                                "Choose another quality or install FFmpeg.")
            return

        analysis = self._analysis or {}
        playlist_title = analysis.get("playlist_title") or analysis.get("title") or ""
        mode = self._current_mode()
        number_files = mode in (MODE_PLAYLIST, MODE_SELECTED)

        payload = {
            "tasks": tasks,
            "output_dir": output_dir,
            "quality": quality,
            "make_playlist_folder": self.chk_playlist_folder.isChecked() and mode != MODE_VIDEO,
            "playlist_title": playlist_title,
            "skip_existing": self.chk_skip_existing.isChecked(),
            "retries": int(self.settings.get("retries", 3)),
            "concurrent_fragments": int(self.settings.get("concurrent_fragments", 4)),
            "embed_metadata": bool(self.settings.get("embed_metadata", True)),
            "embed_thumbnail": bool(self.settings.get("embed_thumbnail", False)),
            "number_files": number_files,
            "ffmpeg_status": self.ffmpeg,
        }

        self._total_tasks = len(tasks)
        self._finished_tasks = 0
        self._counts = {"completed": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        self.bar_overall.setValue(0)
        self.bar_current.setValue(0)
        self.bar_current.setFormat("%p%")
        multi = self._total_tasks > 1
        self.bar_overall.setVisible(multi)
        self.lbl_overall.setVisible(multi)
        self.lbl_overall.setText(f"Starting {self._total_tasks} items…")
        self.lbl_current.setText("Preparing…")
        self.lbl_stats.setText("")
        self.lbl_counts.setText("")
        self.btn_formats.setChecked(False)
        self.progress_card.setVisible(True)
        self._update_counts_label()
        self._scroll_to_bottom()
        self._log("=" * 60)
        self._log(f"Starting download: {self._total_tasks} item(s), quality '{quality}', "
                  f"mode '{mode}', output '{output_dir}'")

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
        title = payload.get("title") or "—"
        self.lbl_current.setText(title)
        percent = payload.get("percent")
        if percent is not None:
            self.bar_current.setValue(int(max(0.0, min(1.0, percent)) * 1000))
            self.bar_current.setFormat(f"{percent * 100:.1f}%")
        downloaded = payload.get("downloaded_bytes") or 0
        total = payload.get("total_bytes")
        speed = payload.get("speed")
        eta = payload.get("eta")
        speed_txt = f"{human_bytes(speed)}/s" if speed else "—"
        total_txt = human_bytes(total) if total else "unknown"
        eta_txt = human_duration(eta) if eta is not None else "—"
        self.lbl_stats.setText(
            f"{human_bytes(downloaded)} / {total_txt}   •   {speed_txt}   •   ETA {eta_txt}"
        )
        # Overall progress: finished items + fraction of the current one.
        frac = percent if percent is not None else 0.0
        if self._total_tasks:
            overall = (self._finished_tasks + max(0.0, min(1.0, frac))) / self._total_tasks
            self.bar_overall.setValue(int(overall * 1000))
            self.lbl_overall.setText(
                f"{self._finished_tasks}/{self._total_tasks}  ({overall * 100:.0f}%)"
            )

    @Slot(dict)
    def _on_postproc(self, payload: Dict[str, Any]) -> None:
        pp = payload.get("postprocessor") or ""
        status = payload.get("status") or ""
        if pp:
            self.statusBar().showMessage(f"{status}: {pp}")

    @Slot(dict)
    def _on_video_done(self, result: Dict[str, Any]) -> None:
        status = result.get("status")
        if status in self._counts:
            self._counts[status] += 1
        elif status == "cancelled":
            self._counts["cancelled"] += 1
        self._finished_tasks += 1
        self._update_counts_label()
        self._update_row_status(result)
        if self._total_tasks:
            self.bar_overall.setValue(int(self._finished_tasks / self._total_tasks * 1000))

    def _update_counts_label(self) -> None:
        c = self._counts
        self.lbl_counts.setText(
            f"{c['completed']} done   •   {c['skipped']} skipped   •   "
            f"{c['failed']} failed   •   {c['cancelled']} cancelled"
        )

    def _update_row_status(self, result: Dict[str, Any]) -> None:
        index = result.get("index")
        title = result.get("title")
        for row, entry in enumerate(self._entries):
            match = (index is not None and entry.get("index") == index) or \
                    (title and entry.get("title") == title)
            if match:
                labels = {
                    STATUS_COMPLETED: ("Done", "#1b5e20", True),
                    STATUS_SKIPPED: ("Skipped", "#555", True),
                    STATUS_FAILED: ("Failed", "#8a1c1c", False),
                    "cancelled": ("Cancelled", "#8a5a00", False),
                }
                text, color, tick = labels.get(result.get("status"), ("—", "#333", False))
                item = self.playlist_table.item(row, COL_STATUS)
                if item:
                    item.setText(text)
                    item.setForeground(QColor(color))
                chk = self.playlist_table.item(row, COL_CHECK)
                if chk and result.get("status") in (STATUS_COMPLETED, STATUS_SKIPPED):
                    chk.setCheckState(Qt.CheckState.Checked)
                break

    @Slot(dict)
    def _on_download_finished(self, summary: Dict[str, Any]) -> None:
        self._last_summary = summary
        completed = summary.get("completed", 0)
        skipped = summary.get("skipped", 0)
        failed = summary.get("failed", 0)
        cancelled = summary.get("cancelled", 0)
        elapsed = summary.get("elapsed", 0)
        self._log("-" * 60)
        self._log(
            f"Summary: {completed} completed, {skipped} skipped, {failed} failed, "
            f"{cancelled} cancelled in {human_duration(elapsed)}."
        )
        for r in summary.get("results", []):
            if r.get("status") == STATUS_FAILED:
                self._log(f"  FAILED: {r.get('title')} — {r.get('error')}")
        if self._total_tasks > 1:
            self.bar_overall.setValue(1000)
            self.lbl_overall.setText(f"{self._total_tasks}/{self._total_tasks}  (100%)")
        self.lbl_current.setText("Finished")
        self.statusBar().showMessage("Finished.")
        self._update_button_states()

        if summary.get("cancelled_job"):
            QMessageBox.information(self, "Cancelled", "The download job was cancelled.")
        elif failed:
            QMessageBox.warning(
                self, "Completed with errors",
                f"{completed} downloaded, {skipped} skipped, {failed} failed.\n"
                "See the details panel for more. Use 'Retry failed' to try again.",
            )
        else:
            QMessageBox.information(
                self, "Download complete",
                f"{completed} downloaded, {skipped} skipped.\n"
                f"Saved to:\n{summary.get('output_dir')}",
            )

    @Slot(str)
    def _on_download_failed(self, message: str) -> None:
        self._log(f"Download job failed: {message}")
        self.statusBar().showMessage("Failed.")
        self._update_button_states()
        QMessageBox.critical(self, "Download failed", message)

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
            self._log("Cancellation requested. Finishing the current fragment safely…")
            self.btn_cancel.setEnabled(False)
            self.statusBar().showMessage("Cancelling…")
            self._dl_worker.cancel()

    @Slot()
    def _on_retry(self) -> None:
        if not self._last_summary:
            return
        failed = [r for r in self._last_summary.get("results", [])
                  if r.get("status") == STATUS_FAILED and r.get("url")]
        if not failed:
            QMessageBox.information(self, "Nothing to retry", "There are no failed downloads.")
            return
        self._log(f"Retrying {len(failed)} failed download(s)…")
        retry_tasks = [
            {"url": r["url"], "title": r.get("title", ""), "index": r.get("index")}
            for r in failed
        ]
        self._start_download(retry_tasks)

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    @Slot(str)
    def _log(self, message: str) -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
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
        if self._fmt_thread is not None:
            self._fmt_thread.quit()
            self._fmt_thread.wait(5000)
        self._save_settings()
        event.accept()
