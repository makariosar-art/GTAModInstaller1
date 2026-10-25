from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...installer import BackupEntry
from ...utils import files_word, human_size, plural_ru
from ..theme import set_pill
from ..widgets import EmptyState, ElidedLabel, caption


class _BackupCard(QFrame):
    rollback = Signal(object)
    remove = Signal(object)

    def __init__(self, entry: BackupEntry, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("BackupCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 13, 12)
        layout.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(9)
        title = QLabel(entry.title)
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 14px;")
        top.addWidget(title, 1)
        pill = QLabel("")
        set_pill(
            pill,
            {
                "installed": "ok",
                "rolled_back": "muted",
                "failed": "bad",
                "in_progress": "warn",
            }.get(entry.status, "muted"),
        )
        pill.setText(entry.status_label.upper())
        top.addWidget(pill)
        text.addLayout(top)
        meta = QLabel(
            f"{entry.date_label}   ·   {entry.file_count} {files_word(entry.file_count)}"
            f"   ·   {entry.replaced_count} "
            f"{plural_ru(entry.replaced_count, 'замена', 'замены', 'замен')}"
            f"   ·   {human_size(entry.size)}"
        )
        meta.setObjectName("Dim")
        meta.setStyleSheet("font-size: 11px;")
        text.addWidget(meta)
        path = ElidedLabel(entry.game_path or "Папка игры не записана", Qt.ElideMiddle)
        path.setObjectName("Muted")
        path.setStyleSheet("font-size: 11px;")
        path.setToolTip(entry.game_path)
        text.addWidget(path)
        layout.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(5)
        rollback = QPushButton("Откатить")
        rollback.setObjectName("Secondary")
        rollback.setCursor(Qt.PointingHandCursor)
        rollback.setStyleSheet("padding: 6px 11px; font-size: 12px;")
        rollback.setEnabled(entry.status != "rolled_back")
        rollback.clicked.connect(lambda: self.rollback.emit(self.entry))
        actions.addWidget(rollback)
        delete = QPushButton("Удалить")
        delete.setObjectName("Ghost")
        delete.setCursor(Qt.PointingHandCursor)
        delete.setStyleSheet("color: #8e6668;")
        delete.clicked.connect(lambda: self.remove.emit(self.entry))
        actions.addWidget(delete)
        actions.addStretch(1)
        layout.addLayout(actions)


class BackupsPage(QWidget):
    rollback_requested = Signal(object)
    delete_requested = Signal(object)
    refresh_requested = Signal()
    open_folder_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[BackupEntry] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(caption("История установок"))
        self.summary = QLabel("Копий пока нет")
        self.summary.setObjectName("Muted")
        self.summary.setStyleSheet("font-size: 11px;")
        tools.addWidget(self.summary)
        tools.addStretch(1)
        folder = QPushButton("Папка с копиями")
        folder.setObjectName("Ghost")
        folder.setCursor(Qt.PointingHandCursor)
        folder.clicked.connect(self.open_folder_requested.emit)
        tools.addWidget(folder)
        refresh = QPushButton("Обновить")
        refresh.setObjectName("Secondary")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.refresh_requested.emit)
        tools.addWidget(refresh)
        root.addLayout(tools)

        self.empty = EmptyState(
            "Резервных копий нет",
            "После первой установки здесь появятся сессии с возможностью полного отката.",
            "⟲",
        )
        root.addWidget(self.empty, 1)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(9)
        self.body_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        self.scroll.setVisible(False)
        root.addWidget(self.scroll, 1)

        note = QLabel(
            "Откат возвращает заменённые файлы из копии и удаляет файлы, созданные установщиком. "
            "Установщик не хранит копии неизменённых файлов."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 10px;")
        root.addWidget(note)

    def set_backups(self, entries: list[BackupEntry]) -> None:
        self._entries = entries
        while self.body_layout.count() > 1:
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        index = 0
        for entry in entries:
            card = _BackupCard(entry)
            card.rollback.connect(self.rollback_requested.emit)
            card.remove.connect(self.delete_requested.emit)
            self.body_layout.insertWidget(index, card)
            index += 1
        total = sum(entry.size for entry in entries)
        sessions = plural_ru(len(entries), "сессия", "сессии", "сессий")
        self.summary.setText(
            f"{len(entries)} {sessions} · {human_size(total)}" if entries else "Копий пока нет"
        )
        self.empty.setVisible(not entries)
        self.scroll.setVisible(bool(entries))

    def set_busy(self, busy: bool) -> None:
        for card in self.body.findChildren(_BackupCard):
            for button in card.findChildren(QPushButton):
                button.setEnabled(not busy)
