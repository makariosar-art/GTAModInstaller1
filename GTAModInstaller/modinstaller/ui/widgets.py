from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..deps_defs import dep_name
from ..game import GameInfo
from ..site import ModListing
from .image_loader import AsyncCover
from .models import SourceState
from .theme import COLORS, set_pill


def _short_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн".replace(".0 ", " ")
    if value >= 1_000:
        return f"{value / 1_000:.0f} тыс."
    return str(value)


def _short_summary(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (cut or text[:limit]).rstrip() + "…"


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionCaption")
    return label


def vspace(height: int) -> QWidget:
    widget = QWidget()
    widget.setFixedHeight(height)
    widget.setAttribute(Qt.WA_TransparentForMouseEvents)
    return widget


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.ElideRight, parent=None) -> None:
        super().__init__(text, parent)
        self._mode = mode
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._update_text()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()

    def _update_text(self) -> None:
        metrics = QFontMetrics(self.font())
        available = max(10, self.width() - 2)
        super().setText(metrics.elidedText(self._full, self._mode, available))


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = 16) -> None:
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            widget = item.widget()
            space_x = spacing
            space_y = spacing
            if widget is not None:
                hint = widget.sizeHint()
                minimum = widget.minimumSize()
                maximum = widget.maximumSize()
                width = max(hint.width(), minimum.width())
                width = min(width, maximum.width())
                if widget.hasHeightForWidth():
                    height = widget.heightForWidth(width)
                else:
                    height = hint.height()
                height = max(height, minimum.height())
                height = min(height, maximum.height())
                item_size = QSize(width, height)
            else:
                item_size = item.sizeHint()
            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))
            x = next_x
            line_height = max(line_height, item_size.height())
        return y + line_height - rect.y() + margins.bottom()


class DropZone(QFrame):
    paths_dropped = Signal(list)
    browse_archive = Signal()
    browse_folder = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(148)
        self.setProperty("drag", "false")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 20, 24, 20)
        layout.setSpacing(20)

        icon = QLabel("⇩")
        icon.setObjectName("DropIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(58, 58)
        layout.addWidget(icon, 0, Qt.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(5)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title = QLabel("Перетащите мод сюда")
        title.setObjectName("HeroTitle")
        title_row.addWidget(title)
        badge = QLabel("АВТО-РАСКЛАДКА")
        badge.setStyleSheet(
            f"color: {COLORS['accent']}; background: {COLORS['accent_soft']};"
            f"border: 1px solid {COLORS['accent_border']}; border-radius: 8px;"
            "padding: 3px 8px; font-size: 9px; font-weight: 700; letter-spacing: 1px;"
        )
        title_row.addWidget(badge)
        title_row.addStretch(1)
        text_box.addLayout(title_row)
        subtitle = QLabel(
            "ZIP, RAR, 7Z, OIV или папка с модом. Установщик распакует архив, "
            "разложит файлы по папкам игры и предложит зависимости."
        )
        subtitle.setObjectName("Dim")
        subtitle.setWordWrap(True)
        text_box.addWidget(subtitle)
        formats = QLabel("GTA5-Mods.com  ·  ASI / SHVDN  ·  Add-on DLC  ·  LML")
        formats.setObjectName("SectionCaption")
        formats.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 1px;")
        text_box.addWidget(formats)
        layout.addLayout(text_box, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(8)
        choose_file = QPushButton("Выбрать архив")
        choose_file.setObjectName("Primary")
        choose_file.setCursor(Qt.PointingHandCursor)
        choose_file.clicked.connect(self.browse_archive.emit)
        choose_folder = QPushButton("Выбрать папку")
        choose_folder.setObjectName("Secondary")
        choose_folder.setCursor(Qt.PointingHandCursor)
        choose_folder.clicked.connect(self.browse_folder.emit)
        buttons.addWidget(choose_file)
        buttons.addWidget(choose_folder)
        layout.addLayout(buttons)

    def _set_drag(self, active: bool) -> None:
        self.setProperty("drag", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_drag(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.paths_dropped.emit(paths)
        else:
            event.ignore()


class GameBar(QFrame):
    change_clicked = Signal()
    detect_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("GameBar")
        self.setFixedHeight(62)
        self.setMinimumWidth(360)
        self.setMaximumWidth(430)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 10, 9)
        layout.setSpacing(10)
        self.dot = QFrame()
        self.dot.setObjectName("GameDot")
        self.dot.setFixedSize(10, 10)
        self.dot.setProperty("state", "bad")
        layout.addWidget(self.dot, 0, Qt.AlignVCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        self.title = ElidedLabel("Игра не найдена")
        self.title.setObjectName("CardTitle")
        self.title.setStyleSheet("font-size: 13px;")
        title_row.addWidget(self.title, 1)
        self.version_pill = QLabel("")
        self.version_pill.setVisible(False)
        title_row.addWidget(self.version_pill)
        text_box.addLayout(title_row)
        self.subtitle = ElidedLabel("Укажите папку GTA V")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setStyleSheet("font-size: 11px;")
        text_box.addWidget(self.subtitle)
        layout.addLayout(text_box, 1)
        self.action = QPushButton("Указать")
        self.action.setObjectName("Secondary")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setStyleSheet("padding: 6px 10px; font-size: 12px;")
        self.action.clicked.connect(self.change_clicked.emit)
        layout.addWidget(self.action)
        detect = QPushButton("Найти")
        detect.setObjectName("Ghost")
        detect.setCursor(Qt.PointingHandCursor)
        detect.setStyleSheet("padding: 6px 8px; font-size: 12px;")
        detect.clicked.connect(self.detect_clicked.emit)
        layout.addWidget(detect)
        self.detect_button = detect

    def set_game(self, game: GameInfo | None) -> None:
        if game is None:
            self.setProperty("state", "missing")
            self.dot.setProperty("state", "bad")
            self.title.setText("Игра не найдена")
            self.subtitle.setText("Укажите папку GTA V")
            self.version_pill.setVisible(False)
            self.action.setText("Указать")
        else:
            self.setProperty("state", "ok")
            self.dot.setProperty("state", "ok")
            self.title.setText(game.label)
            bits = []
            if game.version:
                bits.append(f"v{game.version}")
            bits.append(game.source or "вручную")
            if not game.writable:
                bits.append("только чтение")
            self.subtitle.setText("  ·  ".join(bits))
            self.subtitle.setToolTip(str(game.path))
            self.action.setText("Сменить")
            if game.version:
                self.version_pill.setText(game.version)
                set_pill(self.version_pill, "accent")
                self.version_pill.setVisible(True)
            else:
                self.version_pill.setVisible(False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)

    def set_enabled_actions(self, enabled: bool) -> None:
        self.action.setEnabled(enabled)
        self.detect_button.setEnabled(enabled)


class SourceCard(QFrame):
    plan_requested = Signal(str)
    remove_requested = Signal(str)
    retry_requested = Signal(str)
    readme_requested = Signal(str)

    def __init__(self, state: SourceState, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SourceCard")
        self.key = state.key
        self.setProperty("state", state.status if state.status in {"ready", "error"} else "idle")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.setSpacing(9)
        title = ElidedLabel(state.title)
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 14px;")
        title_row.addWidget(title, 1)
        self.pill = QLabel()
        title_row.addWidget(self.pill)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("Ghost")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(22, 22)
        self.close_button.setToolTip("Убрать из очереди")
        self.close_button.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 13px; padding: 0; border: none;"
        )
        self.close_button.clicked.connect(lambda: self.remove_requested.emit(self.key))
        title_row.addWidget(self.close_button)
        left.addLayout(title_row)
        self.subtitle = ElidedLabel(state.subtitle)
        self.subtitle.setObjectName("Dim")
        self.subtitle.setStyleSheet("font-size: 12px;")
        left.addWidget(self.subtitle)
        origin = "Онлайн-мод" if state.origin == "online" else ("Архив" if state.path and state.path.is_file() else "Папка")
        path_text = str(state.path) if state.path else state.page_url
        meta = ElidedLabel(f"{origin}  ·  {path_text}", Qt.ElideMiddle)
        meta.setObjectName("Muted")
        meta.setStyleSheet("font-size: 11px;")
        meta.setToolTip(path_text)
        left.addWidget(meta)
        self.deps_row = QHBoxLayout()
        self.deps_row.setSpacing(6)
        self.deps_row.setContentsMargins(0, 1, 0, 0)
        self.deps_label = QLabel("")
        self.deps_label.setObjectName("Muted")
        self.deps_label.setStyleSheet("font-size: 11px;")
        self.deps_row.addWidget(self.deps_label)
        self.deps_row.addStretch(1)
        left.addLayout(self.deps_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(360)
        self.progress.setMaximumHeight(5)
        left.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Muted")
        self.progress_label.setStyleSheet("font-size: 11px;")
        self.progress_label.setVisible(False)
        left.addWidget(self.progress_label)
        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorText")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        left.addWidget(self.error_label)
        layout.addLayout(left, 1)

        actions = QVBoxLayout()
        actions.setSpacing(5)
        self.plan_button = QPushButton("План и установка")
        self.plan_button.setObjectName("Secondary")
        self.plan_button.setCursor(Qt.PointingHandCursor)
        self.plan_button.setStyleSheet("padding: 7px 12px; font-size: 12px;")
        self.plan_button.clicked.connect(lambda: self.plan_requested.emit(self.key))
        actions.addWidget(self.plan_button)
        self.readme_button = QPushButton("Инструкция")
        self.readme_button.setObjectName("Ghost")
        self.readme_button.setCursor(Qt.PointingHandCursor)
        self.readme_button.clicked.connect(lambda: self.readme_requested.emit(self.key))
        actions.addWidget(self.readme_button)
        self.retry_button = QPushButton("Повторить")
        self.retry_button.setObjectName("Ghost")
        self.retry_button.setCursor(Qt.PointingHandCursor)
        self.retry_button.clicked.connect(lambda: self.retry_requested.emit(self.key))
        actions.addWidget(self.retry_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.set_state(state)

    def set_state(self, state: SourceState) -> None:
        self.setProperty("state", state.status if state.status in {"ready", "error"} else "idle")
        self.style().unpolish(self)
        self.style().polish(self)
        self.subtitle.setText(state.subtitle)
        preparing = state.status in {"queued", "preparing"}
        self.progress.setVisible(state.status == "preparing")
        self.progress_label.setVisible(state.status == "preparing")
        if state.status == "preparing":
            self.progress.setRange(0, 0) if state.message.startswith("Распаковка") else self.progress.setRange(0, 100)
            self.progress_label.setText(state.message)
        self.error_label.setVisible(state.status == "error")
        if state.status == "error":
            self.error_label.setText(state.error)
            self.error_label.setToolTip(state.hint)
        self.plan_button.setVisible(state.status == "ready")
        self.retry_button.setVisible(state.status == "error")
        has_readme = bool(state.plan and state.plan.readmes)
        self.readme_button.setVisible(has_readme)
        if state.status == "ready":
            set_pill(self.pill, "warn" if state.plan and state.plan.warnings else "accent")
            self.pill.setText("ЗАМЕНА" if state.plan and state.plan.conflict_count else "ГОТОВ")
        elif state.status == "error":
            set_pill(self.pill, "bad")
            self.pill.setText("ОШИБКА")
        elif state.status == "preparing":
            set_pill(self.pill, "info")
            self.pill.setText("АНАЛИЗ")
        else:
            set_pill(self.pill, "muted")
            self.pill.setText("В ОЧЕРЕДИ")
        if state.plan is not None and state.plan.deps:
            names = ", ".join(dep_name(key) for key in sorted(state.plan.deps))
            self.deps_label.setText("Зависимости: " + names)
            self.deps_label.setToolTip(
                "\n".join(
                    f"{dep_name(key)}: {', '.join(reasons)}"
                    for key, reasons in state.plan.dep_reasons.items()
                )
            )
        elif state.status == "ready":
            self.deps_label.setText("Дополнительные зависимости не требуются.")
        else:
            self.deps_label.setText("")


class ModCard(QFrame):
    clicked = Signal(object)

    def __init__(self, listing: ModListing, parent=None) -> None:
        super().__init__(parent)
        self.listing = listing
        self.setObjectName("ModCard")
        self.setFixedWidth(286)
        self.setMinimumHeight(336)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.cover = AsyncCover(height=154)
        self.cover.set_placeholder(listing.tag or "MOD")
        self.cover.set_url(listing.image)
        layout.addWidget(self.cover)
        body = QVBoxLayout()
        body.setContentsMargins(14, 11, 14, 13)
        body.setSpacing(5)
        title = QLabel(listing.title)
        title.setWordWrap(True)
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 14px;")
        title.setMinimumHeight(38)
        body.addWidget(title)
        author = QLabel(listing.author or "Автор неизвестен")
        author.setObjectName("Muted")
        author.setStyleSheet("font-size: 11px;")
        body.addWidget(author)
        summary = _short_summary(listing.summary)
        if summary:
            description = QLabel(summary)
            description.setObjectName("Muted")
            description.setWordWrap(True)
            description.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            description.setStyleSheet("font-size: 11px; line-height: 1.4;")
            body.addWidget(description)
        body.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        if listing.tag:
            tag = QLabel(listing.tag.upper())
            tag.setStyleSheet(
                f"color: {COLORS['text_dim']}; background: rgba(255,255,255,0.045);"
                f"border: 1px solid {COLORS['border']}; border-radius: 7px;"
                "padding: 2px 7px; font-size: 9px; font-weight: 700;"
            )
            bottom.addWidget(tag)
        if listing.version:
            version = QLabel(listing.version)
            version.setObjectName("Muted")
            version.setStyleSheet("font-size: 10px;")
            bottom.addWidget(version)
        bottom.addStretch(1)
        if listing.rating:
            rating = QLabel(f"★ {listing.rating}")
            rating.setStyleSheet(f"color: {COLORS['warning']}; font-size: 11px; font-weight: 600;")
            bottom.addWidget(rating)
        if listing.downloads:
            downloads = QLabel(_short_count(listing.downloads))
            downloads.setObjectName("Muted")
            downloads.setStyleSheet("font-size: 10px;")
            downloads.setToolTip(f"{listing.downloads:,} загрузок".replace(",", " "))
            bottom.addWidget(downloads)
        body.addLayout(bottom)
        layout.addLayout(body)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.listing)
        super().mouseReleaseEvent(event)


class EmptyState(QFrame):
    def __init__(self, title: str, text: str, glyph: str = "◆", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SoftPanel")
        self.setMinimumHeight(170)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 25, 24, 25)
        layout.setSpacing(7)
        layout.addStretch(1)
        icon = QLabel(glyph)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {COLORS['accent']}; font-size: 25px;")
        layout.addWidget(icon)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        text_label = QLabel(text)
        text_label.setObjectName("Muted")
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)
        layout.addStretch(1)


class StatTile(QFrame):
    def __init__(self, value: str, label: str, accent: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 11, 15, 11)
        layout.setSpacing(1)
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        if accent:
            self.value.setStyleSheet(f"color: {COLORS['accent']};")
        layout.addWidget(self.value)
        self.label = QLabel(label.upper())
        self.label.setObjectName("StatLabel")
        layout.addWidget(self.label)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class InfoBanner(QFrame):
    def __init__(self, title: str, text: str = "", kind: str = "warn", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeWarn" if kind == "warn" else "NoticeBad" if kind == "bad" else "Notice")
        self.setProperty("kind", kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(11)
        glyph = QLabel("!" if kind == "warn" else "×" if kind == "bad" else "i")
        glyph.setFixedSize(22, 22)
        glyph.setAlignment(Qt.AlignCenter)
        color = COLORS["warning"] if kind == "warn" else COLORS["danger"] if kind == "bad" else COLORS["accent"]
        glyph.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 11px; font-weight: 800; font-size: 12px;"
        )
        layout.addWidget(glyph, 0, Qt.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        text_box.addWidget(title_label)
        self.text_label = QLabel(text)
        self.text_label.setObjectName("Dim")
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet("font-size: 12px;")
        self.text_label.setVisible(bool(text))
        text_box.addWidget(self.text_label)
        layout.addLayout(text_box, 1)
        self._kind = kind

    def set_text(self, text: str) -> None:
        self.text_label.setText(text)
        self.text_label.setVisible(bool(text))
