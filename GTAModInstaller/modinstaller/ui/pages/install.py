from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...utils import files_word, human_size, plural_ru
from ..models import SourceState
from ..widgets import DropZone, EmptyState, SourceCard, caption


class InstallPage(QWidget):
    paths_dropped = Signal(list)
    browse_archive = Signal()
    browse_folder = Signal()
    install_all = Signal()
    plan_requested = Signal(str)
    remove_requested = Signal(str)
    retry_requested = Signal(str)
    readme_requested = Signal(str)
    clear_requested = Signal()
    online_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._keys: list[str] = []
        self._cards: dict[str, SourceCard] = {}
        self._ready_count = 0
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.drop = DropZone()
        self.drop.paths_dropped.connect(self.paths_dropped.emit)
        self.drop.browse_archive.connect(self.browse_archive.emit)
        self.drop.browse_folder.connect(self.browse_folder.emit)
        root.addWidget(self.drop)

        tools = QHBoxLayout()
        tools.setSpacing(9)
        tools.addWidget(caption("Очередь установки"))
        self.count_label = QLabel("Пусто")
        self.count_label.setObjectName("Muted")
        self.count_label.setStyleSheet("font-size: 11px;")
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        online = QPushButton("Онлайн-каталог")
        online.setObjectName("Ghost")
        online.setCursor(Qt.PointingHandCursor)
        online.clicked.connect(self.online_requested.emit)
        tools.addWidget(online)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.setObjectName("Ghost")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        tools.addWidget(self.clear_button)
        self.install_button = QPushButton("Установить всё")
        self.install_button.setObjectName("Primary")
        self.install_button.setCursor(Qt.PointingHandCursor)
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_all.emit)
        tools.addWidget(self.install_button)
        root.addLayout(tools)

        self.empty = EmptyState(
            "Очередь пуста",
            "Перетащите ZIP, RAR, 7Z, OIV или папку с модом — установщик сам разберёт файлы.",
            "⇩",
        )
        root.addWidget(self.empty, 1)

        self.queue_body = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_body)
        self.queue_layout.setContentsMargins(0, 0, 6, 0)
        self.queue_layout.setSpacing(9)
        self.queue_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.queue_body)
        self.scroll.setVisible(False)
        root.addWidget(self.scroll, 1)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(14)
        for text in (
            "Архивы распаковываются автоматически",
            "Зависимости подбираются по файлам и описанию",
            "Перед записью открывается план установки",
        ):
            label = QLabel("•  " + text)
            label.setObjectName("Muted")
            label.setStyleSheet("font-size: 11px;")
            hint_row.addWidget(label)
        hint_row.addStretch(1)
        root.addLayout(hint_row)

    def sync(self, states: list[SourceState]) -> None:
        keys = [state.key for state in states]
        if keys != self._keys:
            self._rebuild(states)
        else:
            for state in states:
                card = self._cards.get(state.key)
                if card is not None:
                    card.set_state(state)
        ready = [state for state in states if state.ready]
        self._ready_count = len(ready)
        files = sum(len(state.plan.enabled_items) for state in ready if state.plan)
        size = sum(state.plan.total_size for state in ready if state.plan)
        if states:
            extra = (
                f" · {files} {files_word(files)} · {human_size(size)}" if ready else ""
            )
            mods = plural_ru(len(states), "мод", "мода", "модов")
            self.count_label.setText(f"{len(states)} {mods}{extra}")
        else:
            self.count_label.setText("Пусто")
        self.clear_button.setEnabled(not self._busy and bool(states))
        self.install_button.setEnabled(not self._busy and bool(ready))
        self.install_button.setText(
            f"Установить всё · {len(ready)}" if len(ready) > 1 else "Установить всё"
        )

    def _rebuild(self, states: list[SourceState]) -> None:
        while self.queue_layout.count() > 1:
            item = self.queue_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        self._keys = [state.key for state in states]
        index = 0
        for state in states:
            card = SourceCard(state)
            card.plan_requested.connect(self.plan_requested.emit)
            card.remove_requested.connect(self.remove_requested.emit)
            card.retry_requested.connect(self.retry_requested.emit)
            card.readme_requested.connect(self.readme_requested.emit)
            self.queue_layout.insertWidget(index, card)
            self._cards[state.key] = card
            index += 1
        self.empty.setVisible(not states)
        self.scroll.setVisible(bool(states))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.install_button.setEnabled(not busy and self._ready_count > 0)
        self.clear_button.setEnabled(not busy and bool(self._keys))
