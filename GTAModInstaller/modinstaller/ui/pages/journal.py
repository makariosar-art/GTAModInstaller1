from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..widgets import caption


class JournalPage(QWidget):
    open_file_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(caption("Журнал операций"))
        hint = QLabel("Скачивания, анализ, установка и откаты")
        hint.setObjectName("Muted")
        hint.setStyleSheet("font-size: 11px;")
        tools.addWidget(hint)
        tools.addStretch(1)
        copy = QPushButton("Копировать")
        copy.setObjectName("Ghost")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(self._copy)
        tools.addWidget(copy)
        clear = QPushButton("Очистить окно")
        clear.setObjectName("Ghost")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_requested.emit)
        tools.addWidget(clear)
        open_file = QPushButton("Открыть файл лога")
        open_file.setObjectName("Secondary")
        open_file.setCursor(Qt.PointingHandCursor)
        open_file.clicked.connect(self.open_file_requested.emit)
        tools.addWidget(open_file)
        root.addLayout(tools)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = self.view.font()
        font.setFamily("Cascadia Mono")
        font.setPointSize(9)
        self.view.setFont(font)
        self.view.document().setMaximumBlockCount(5000)
        root.addWidget(self.view, 1)

    def append(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.view.appendPlainText(f"[{stamp}]  {message}")
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self) -> None:
        self.view.clear()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())
