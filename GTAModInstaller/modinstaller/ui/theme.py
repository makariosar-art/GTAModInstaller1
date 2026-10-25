from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ..config import resource_path

COLORS = {
    "bg": "#0A0D0A",
    "bg_deep": "#070907",
    "sidebar": "#0C110C",
    "panel": "#111711",
    "panel_alt": "#151D15",
    "panel_soft": "#182118",
    "border": "#243024",
    "border_soft": "#1B251B",
    "text": "#EEF4E8",
    "text_dim": "#A5B3A0",
    "muted": "#7E8D7A",
    "accent": "#C6FF3D",
    "accent_dark": "#9BD41E",
    "accent_soft": "rgba(198, 255, 61, 0.12)",
    "accent_border": "rgba(198, 255, 61, 0.35)",
    "info": "#64D8FF",
    "warning": "#FFC15A",
    "danger": "#FF6273",
    "success": "#A8F06A",
    "shadow": "rgba(0, 0, 0, 0.45)",
}


def _pick_font(families: list[str], fallback: str = "Segoe UI") -> str:
    available = set(QFontDatabase.families())
    for family in families:
        if family in available:
            return family
    return fallback


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    body_family = _pick_font(["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"])
    font = QFont(body_family, 10)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Base, QColor(COLORS["panel"]))
    palette.setColor(QPalette.AlternateBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.Button, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor("#0A0D0A"))
    palette.setColor(QPalette.ToolTipBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(COLORS["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet())


def _stylesheet() -> str:
    c = COLORS
    return _with_assets(f"""
    * {{
        outline: none;
    }}
    QWidget {{
        color: {c['text']};
        font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", Arial;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background-color: {c['bg']};
    }}
    QWidget#Root {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #0B0F0B, stop:0.55 #090C09, stop:1 #0A100A);
    }}
    QWidget#Sidebar {{
        background-color: {c['sidebar']};
        border-right: 1px solid {c['border_soft']};
    }}
    QWidget#Content {{
        background: transparent;
    }}
    QWidget#Page {{
        background: transparent;
    }}
    QLabel {{
        background: transparent;
        color: {c['text']};
    }}
    QLabel#BrandMark {{
        color: #0A0D0A;
        background-color: {c['accent']};
        border-radius: 11px;
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 23px;
        font-weight: 800;
    }}
    QLabel#BrandTitle {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-weight: 700;
        font-size: 17px;
        letter-spacing: 1px;
        color: {c['text']};
    }}
    QLabel#BrandSub {{
        color: {c['muted']};
        font-size: 9px;
        letter-spacing: 2px;
        font-weight: 600;
    }}
    QLabel#SectionCaption {{
        color: {c['muted']};
        font-size: 9px;
        letter-spacing: 1.6px;
        font-weight: 700;
    }}
    QLabel#Eyebrow {{
        color: {c['accent']};
        font-size: 10px;
        letter-spacing: 2px;
        font-weight: 700;
    }}
    QLabel#PageTitle {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 28px;
        font-weight: 700;
        color: {c['text']};
    }}
    QLabel#PageSubtitle {{
        color: {c['text_dim']};
        font-size: 13px;
    }}
    QLabel#CardTitle {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 16px;
        font-weight: 600;
    }}
    QLabel#DialogTitle {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 24px;
        font-weight: 700;
    }}
    QLabel#HeroTitle {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 21px;
        font-weight: 600;
    }}
    QLabel#Muted {{
        color: {c['muted']};
    }}
    QLabel#Dim {{
        color: {c['text_dim']};
    }}
    QLabel#Accent {{
        color: {c['accent']};
        font-weight: 600;
    }}
    QLabel#Mono {{
        font-family: "Cascadia Mono", "Consolas", "Courier New";
        color: {c['text_dim']};
        font-size: 12px;
    }}
    QLabel#StatValue {{
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-size: 20px;
        font-weight: 700;
        color: {c['text']};
    }}
    QLabel#StatLabel {{
        color: {c['muted']};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.8px;
    }}
    QLabel#ErrorText {{
        color: {c['danger']};
        font-size: 12px;
    }}
    QLabel#WarningText {{
        color: {c['warning']};
        font-size: 12px;
    }}
    QLabel[pill="true"] {{
        border-radius: 9px;
        padding: 3px 9px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.4px;
    }}
    QLabel[pill="ok"] {{
        color: {c['success']};
        background: rgba(168, 240, 106, 0.10);
        border: 1px solid rgba(168, 240, 106, 0.24);
    }}
    QLabel[pill="warn"] {{
        color: {c['warning']};
        background: rgba(255, 193, 90, 0.10);
        border: 1px solid rgba(255, 193, 90, 0.22);
    }}
    QLabel[pill="bad"] {{
        color: {c['danger']};
        background: rgba(255, 98, 115, 0.10);
        border: 1px solid rgba(255, 98, 115, 0.24);
    }}
    QLabel[pill="info"] {{
        color: {c['info']};
        background: rgba(100, 216, 255, 0.09);
        border: 1px solid rgba(100, 216, 255, 0.22);
    }}
    QLabel[pill="muted"] {{
        color: {c['text_dim']};
        background: rgba(255, 255, 255, 0.045);
        border: 1px solid {c['border']};
    }}
    QLabel[pill="accent"] {{
        color: {c['accent']};
        background: {c['accent_soft']};
        border: 1px solid {c['accent_border']};
    }}
    QFrame#Card, QFrame#Panel {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 14px;
    }}
    QFrame#PanelAlt {{
        background-color: {c['panel_alt']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#SoftPanel {{
        background-color: rgba(255, 255, 255, 0.025);
        border: 1px solid {c['border_soft']};
        border-radius: 10px;
    }}
    QFrame#Divider {{
        background-color: {c['border_soft']};
        max-height: 1px;
        min-height: 1px;
        border: none;
    }}
    QFrame#GameBar {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#GameBar[state="missing"] {{
        border: 1px solid rgba(255, 98, 115, 0.30);
        background-color: rgba(255, 98, 115, 0.045);
    }}
    QFrame#GameDot {{
        border-radius: 5px;
        background-color: {c['muted']};
        border: none;
    }}
    QFrame#GameDot[state="ok"] {{
        background-color: {c['success']};
    }}
    QFrame#GameDot[state="bad"] {{
        background-color: {c['danger']};
    }}
    QFrame#DropZone {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(198, 255, 61, 0.055), stop:1 rgba(198, 255, 61, 0.012));
        border: 1px solid {c['border']};
        border-radius: 18px;
    }}
    QFrame#DropZone[drag="true"] {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 rgba(198, 255, 61, 0.16), stop:1 rgba(198, 255, 61, 0.05));
        border: 1px solid {c['accent']};
    }}
    QLabel#DropIcon {{
        color: {c['accent']};
        background-color: {c['accent_soft']};
        border: 1px solid {c['accent_border']};
        border-radius: 28px;
        font-family: "Segoe UI Symbol", "Segoe UI";
        font-size: 26px;
        font-weight: 600;
    }}
    QFrame#SourceCard {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#SourceCard:hover {{
        border: 1px solid {c['border']};
    }}
    QFrame#SourceCard[state="error"] {{
        border: 1px solid rgba(255, 98, 115, 0.35);
    }}
    QFrame#SourceCard[state="ready"] {{
        border: 1px solid rgba(198, 255, 61, 0.16);
    }}
    QFrame#ModCard {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 14px;
    }}
    QFrame#ModCard:hover {{
        border: 1px solid {c['accent_border']};
        background-color: {c['panel_alt']};
    }}
    QFrame#StatCard {{
        background-color: rgba(255, 255, 255, 0.025);
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#Notice {{
        background-color: {c['accent_soft']};
        border: 1px solid {c['accent_border']};
        border-radius: 10px;
    }}
    QFrame#NoticeWarn {{
        background-color: rgba(255, 193, 90, 0.07);
        border: 1px solid rgba(255, 193, 90, 0.24);
        border-radius: 10px;
    }}
    QFrame#NoticeBad {{
        background-color: rgba(255, 98, 115, 0.07);
        border: 1px solid rgba(255, 98, 115, 0.24);
        border-radius: 10px;
    }}
    QPushButton {{
        background-color: transparent;
        color: {c['text']};
        border: none;
        border-radius: 8px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: rgba(255, 255, 255, 0.06);
    }}
    QPushButton:pressed {{
        background-color: rgba(255, 255, 255, 0.09);
    }}
    QPushButton:disabled {{
        color: #5B6958;
    }}
    QPushButton#Primary {{
        background-color: {c['accent']};
        color: #0A0D0A;
        border: none;
        border-radius: 9px;
        padding: 10px 20px;
        font-weight: 700;
    }}
    QPushButton#Primary:hover {{
        background-color: #D2FF69;
    }}
    QPushButton#Primary:pressed {{
        background-color: {c['accent_dark']};
    }}
    QPushButton#Primary:disabled {{
        background-color: #2A3321;
        color: #6F7D63;
    }}
    QPushButton#Secondary {{
        background-color: {c['panel_soft']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 9px 16px;
        font-weight: 600;
    }}
    QPushButton#Secondary:hover {{
        border: 1px solid {c['accent_border']};
        background-color: #1C261C;
    }}
    QPushButton#Ghost {{
        background-color: transparent;
        border: 1px solid transparent;
        color: {c['text_dim']};
        border-radius: 8px;
        padding: 7px 11px;
        font-weight: 600;
    }}
    QPushButton#Ghost:hover {{
        color: {c['text']};
        background-color: rgba(255, 255, 255, 0.05);
    }}
    QPushButton#Danger {{
        background-color: rgba(255, 98, 115, 0.10);
        border: 1px solid rgba(255, 98, 115, 0.26);
        color: #FF9AA5;
        border-radius: 8px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton#Danger:hover {{
        background-color: rgba(255, 98, 115, 0.18);
        color: {c['danger']};
    }}
    QPushButton#Link {{
        color: {c['accent']};
        background: transparent;
        border: none;
        padding: 3px 0;
        text-align: left;
        font-weight: 600;
    }}
    QPushButton#Link:hover {{
        color: #DCFF86;
        background: transparent;
        text-decoration: underline;
    }}
    QPushButton#Nav {{
        text-align: left;
        padding: 10px 13px;
        border-radius: 9px;
        color: {c['text_dim']};
        font-weight: 600;
        border: 1px solid transparent;
        background: transparent;
        font-size: 13px;
    }}
    QPushButton#Nav:hover {{
        color: {c['text']};
        background-color: rgba(255, 255, 255, 0.045);
    }}
    QPushButton#Nav:checked {{
        color: {c['accent']};
        background-color: rgba(198, 255, 61, 0.10);
        border: 1px solid rgba(198, 255, 61, 0.22);
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{
        background-color: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 9px;
        padding: 8px 12px;
        selection-background-color: {c['accent']};
        selection-color: #0A0D0A;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {c['accent_border']};
        background-color: #172017;
    }}
    QLineEdit:disabled {{
        color: {c['muted']};
        background-color: rgba(255, 255, 255, 0.02);
    }}
    QPushButton#SearchGo {{
        background-color: {c['accent']};
        color: #0A0D0A;
        border-radius: 9px;
        padding: 8px 18px;
        font-weight: 700;
    }}
    QPushButton#SearchGo:hover {{
        background-color: #D2FF69;
    }}
    QCheckBox {{
        color: {c['text']};
        spacing: 10px;
        font-weight: 500;
    }}
    QCheckBox::indicator {{
        width: 20px;
        height: 20px;
        border-radius: 6px;
        border: 1px solid {c['border']};
        background-color: {c['panel_alt']};
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {c['accent_border']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {c['accent']};
        border: 1px solid {c['accent']};
        image: url("{{CHECK_ICON}}");
    }}
    QProgressBar {{
        background-color: rgba(255, 255, 255, 0.055);
        border: none;
        border-radius: 3px;
        max-height: 6px;
        min-height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 3px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #2C382B;
        border-radius: 4px;
        min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #3C4B39;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
    }}
    QScrollBar::handle:horizontal {{
        background: #2C382B;
        border-radius: 4px;
        min-width: 32px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QTableWidget {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 10px;
        gridline-color: {c['border_soft']};
        alternate-background-color: #121912;
        selection-background-color: rgba(198, 255, 61, 0.13);
        selection-color: {c['text']};
    }}
    QTableWidget::item {{
        padding: 8px 10px;
        border: none;
    }}
    QHeaderView::section {{
        background-color: {c['panel_alt']};
        color: {c['text_dim']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 8px 10px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.6px;
    }}
    QTableCornerButton::section {{
        background-color: {c['panel_alt']};
        border: none;
    }}
    QToolTip {{
        background-color: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 6px 9px;
    }}
    QMenu {{
        background-color: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 16px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: rgba(198, 255, 61, 0.12);
        color: {c['accent']};
    }}
    QStatusBar {{
        background: {c['bg']};
        color: {c['muted']};
    }}
    QFrame#StatusStrip {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 10px;
    }}
    QFrame#SidebarDivider {{
        background-color: {c['border_soft']};
        max-height: 1px;
        border: none;
    }}
    QFrame#Sidebar {{
        background-color: {c['sidebar']};
        border-right: 1px solid {c['border_soft']};
    }}
    QPushButton#Chip {{
        background-color: transparent;
        color: {c['text_dim']};
        border: 1px solid {c['border']};
        border-radius: 13px;
        padding: 6px 14px;
        font-size: 11px;
        font-weight: 600;
    }}
    QPushButton#Chip:hover {{
        color: {c['text']};
        border-color: {c['border']};
        background-color: {c['panel']};
    }}
    QPushButton#Chip:checked {{
        color: {c['accent']};
        border: 1px solid {c['accent_border']};
        background-color: {c['accent_soft']};
    }}
    QPushButton#Chip:disabled {{
        color: {c['muted']};
    }}
    QFrame#BackupCard {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#BackupCard:hover {{
        border: 1px solid {c['border']};
    }}
    QFrame#ModCard {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#ModCard:hover {{
        border: 1px solid {c['border']};
    }}
    QFrame#DepRow {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#DepRow:hover {{
        border: 1px solid {c['border']};
    }}
    QLabel#DepBadge {{
        background-color: {c['panel_soft']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        color: {c['accent']};
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-weight: 700;
        font-size: 15px;
    }}
    QLabel#Bullet {{
        color: {c['accent']};
        font-size: 15px;
    }}
    QFrame#SearchBar {{
        background-color: {c['panel']};
        border: 1px solid {c['border_soft']};
        border-radius: 12px;
    }}
    QFrame#HeroBanner {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(198, 255, 61, 0.10), stop:0.5 rgba(198, 255, 61, 0.035), stop:1 rgba(100, 216, 255, 0.03));
        border: 1px solid {c['border']};
        border-radius: 16px;
    }}
    QLabel#HeroGlyph {{
        color: {c['accent']};
        font-family: "Bahnschrift", "Segoe UI Variable Display", "Segoe UI";
        font-weight: 800;
        font-size: 44px;
    }}
    """)


def _with_assets(css: str) -> str:
    icon = resource_path("assets", "check.svg").as_posix()
    return css.replace("{CHECK_ICON}", icon)


def apply_dark_titlebar(widget: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 19, ctypes.byref(value), ctypes.sizeof(value)
        )
        color = 0x000D0A0B  # COLORREF (BGR)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 35, ctypes.byref(ctypes.c_int(color)), ctypes.sizeof(ctypes.c_int)
        )
        text = 0x00E8F4EE
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 36, ctypes.byref(ctypes.c_int(text)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


def set_pill(label, kind: str) -> None:
    label.setStyleSheet(_pill_stylesheet(kind))


def make_pill(text: str, kind: str = "muted"):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    label = QLabel(text)
    label.setStyleSheet(_pill_stylesheet(kind))
    label.setAlignment(Qt.AlignCenter)
    return label


def _pill_stylesheet(kind: str) -> str:
    c = COLORS
    palette = {
        "ok": (c["success"], "rgba(168, 240, 106, 0.10)", "rgba(168, 240, 106, 0.24)"),
        "warn": (c["warning"], "rgba(255, 193, 90, 0.10)", "rgba(255, 193, 90, 0.22)"),
        "bad": (c["danger"], "rgba(255, 98, 115, 0.10)", "rgba(255, 98, 115, 0.24)"),
        "info": (c["info"], "rgba(100, 216, 255, 0.09)", "rgba(100, 216, 255, 0.22)"),
        "accent": (c["accent"], c["accent_soft"], c["accent_border"]),
        "muted": (c["text_dim"], "rgba(255,255,255,0.045)", c["border"]),
    }
    fg, bg, border = palette.get(kind, palette["muted"])
    return (
        f"color: {fg}; background: {bg}; border: 1px solid {border};"
        "border-radius: 9px; padding: 3px 9px; font-size: 10px; font-weight: 700;"
    )
