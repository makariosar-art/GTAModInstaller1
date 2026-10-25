from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...installer import InstalledMod
from ...utils import files_word, human_size, plural_ru
from ..theme import COLORS, set_pill
from ..widgets import EmptyState, ElidedLabel, caption


class _ModCard(QFrame):
    remove = Signal(object)

    def __init__(self, mod: InstalledMod, parent=None) -> None:
        super().__init__(parent)
        self.mod = mod
        self.setObjectName("ModCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 13, 12)
        layout.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(9)
        title = ElidedLabel(mod.title)
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 14px;")
        title.setToolTip(mod.title)
        top.addWidget(title, 1)
        pill = QLabel("")
        set_pill(pill, "info" if mod.dependency else "ok")
        pill.setText("ЗАВИСИМОСТЬ" if mod.dependency else "УСТАНОВЛЕН")
        top.addWidget(pill)
        text.addLayout(top)

        bits = [mod.date_label, f"{mod.file_count} {files_word(mod.file_count)}"]
        if mod.replaced_count:
            bits.append(
                f"{mod.replaced_count} "
                f"{plural_ru(mod.replaced_count, 'замена', 'замены', 'замен')}"
            )
        bits.append(human_size(mod.size))
        meta = QLabel("   ·   ".join(bits))
        meta.setObjectName("Dim")
        meta.setStyleSheet("font-size: 11px;")
        text.addWidget(meta)

        if mod.unprotected_count:
            warning = QLabel(
                f"{mod.unprotected_count} "
                f"{plural_ru(mod.unprotected_count, 'замена', 'замены', 'замен')} "
                "без резервной копии — останутся в игре"
            )
            warning.setStyleSheet(f"color: {COLORS['warning']}; font-size: 10px;")
            text.addWidget(warning)

        path = ElidedLabel(mod.game_path or "Папка игры не записана", Qt.ElideMiddle)
        path.setObjectName("Muted")
        path.setStyleSheet("font-size: 11px;")
        path.setToolTip(mod.game_path)
        text.addWidget(path)
        layout.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(5)
        actions.addStretch(1)
        remove = QPushButton("Удалить")
        remove.setObjectName("Danger")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setStyleSheet("padding: 6px 12px; font-size: 12px;")
        remove.clicked.connect(lambda: self.remove.emit(self.mod))
        actions.addWidget(remove)
        actions.addStretch(1)
        layout.addLayout(actions)


class InstalledModsPage(QWidget):
    remove_requested = Signal(object)
    refresh_requested = Signal()
    backups_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mods: list[InstalledMod] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(caption("Установленные моды"))
        self.summary = QLabel("Пока ничего не установлено")
        self.summary.setObjectName("Muted")
        self.summary.setStyleSheet("font-size: 11px;")
        tools.addWidget(self.summary)
        tools.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск мода")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(self._apply_filter)
        tools.addWidget(self.search)
        backups = QPushButton("Резервные копии")
        backups.setObjectName("Ghost")
        backups.setCursor(Qt.PointingHandCursor)
        backups.clicked.connect(self.backups_requested.emit)
        tools.addWidget(backups)
        self.refresh = QPushButton("Обновить")
        self.refresh.setObjectName("Secondary")
        self.refresh.setCursor(Qt.PointingHandCursor)
        self.refresh.clicked.connect(self.refresh_requested.emit)
        tools.addWidget(self.refresh)
        root.addLayout(tools)

        self.empty = EmptyState(
            "Установленных модов нет",
            "Установите мод из папки, архива или онлайн-каталога — он появится здесь.",
            "▦",
        )
        root.addWidget(self.empty, 1)

        self.no_results = QLabel("По запросу ничего не найдено")
        self.no_results.setObjectName("Muted")
        self.no_results.setAlignment(Qt.AlignCenter)
        self.no_results.setVisible(False)
        root.addWidget(self.no_results)

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
            "Удаление возвращает заменённые файлы из резервных копий и убирает файлы, "
            "добавленные установщиком. Зависимости, установленные вместе с модом, остаются."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 10px;")
        root.addWidget(note)

    def set_mods(self, mods: list[InstalledMod]) -> None:
        self._mods = mods
        while self.body_layout.count() > 1:
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        index = 0
        for mod in mods:
            card = _ModCard(mod)
            card.remove.connect(self.remove_requested.emit)
            self.body_layout.insertWidget(index, card)
            index += 1
        self.empty.setVisible(not mods)
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search.text().strip().casefold()
        visible = 0
        matched_files = 0
        matched_size = 0
        for card in self.body.findChildren(_ModCard):
            haystack = f"{card.mod.title} {card.mod.session_id} {card.mod.game_path}".casefold()
            match = not query or query in haystack
            card.setVisible(match)
            if match:
                visible += 1
                matched_files += card.mod.file_count
                matched_size += card.mod.size
        if not self._mods:
            self.summary.setText("Пока ничего не установлено")
            self.no_results.setVisible(False)
            self.scroll.setVisible(False)
            return
        self.empty.setVisible(False)
        self.no_results.setVisible(visible == 0)
        self.scroll.setVisible(visible > 0)
        if query:
            self.summary.setText(f"Найдено: {visible} из {len(self._mods)}")
        else:
            mods_word = plural_ru(len(self._mods), "мод", "мода", "модов")
            self.summary.setText(
                f"{len(self._mods)} {mods_word} · {matched_files} {files_word(matched_files)}"
                f" · {human_size(matched_size)}"
            )

    def set_busy(self, busy: bool) -> None:
        self.refresh.setEnabled(not busy)
        for card in self.body.findChildren(_ModCard):
            for button in card.findChildren(QPushButton):
                button.setEnabled(not busy)
