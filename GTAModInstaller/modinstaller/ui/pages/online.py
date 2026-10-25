from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...site import ModListing
from ..widgets import EmptyState, FlowLayout, ModCard, caption


class OnlinePage(QWidget):
    search_requested = Signal(str)
    sort_requested = Signal(str)
    load_more_requested = Signal()
    mod_selected = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: list[ModCard] = []
        self._mode = "catalog"
        self._search = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        search_bar = QFrame()
        search_bar.setObjectName("SearchBar")
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(4, 4, 4, 4)
        search_layout.setSpacing(4)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Найти мод: тренер, машина, графика…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._submit_search)
        search_layout.addWidget(self.search_edit, 1)
        search_button = QPushButton("Найти")
        search_button.setObjectName("SearchGo")
        search_button.setCursor(Qt.PointingHandCursor)
        search_button.clicked.connect(self._submit_search)
        search_layout.addWidget(search_button)
        root.addWidget(search_bar)

        filters = QHBoxLayout()
        filters.setSpacing(7)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.chip_catalog = self._chip("Подборка", "catalog", filters)
        self.chip_popular = self._chip("Популярные", "popular", filters)
        self.chip_new = self._chip("Новые", "latest", filters)
        self.chip_catalog.setChecked(True)
        filters.addStretch(1)
        filters.addWidget(caption("GTA5-Mods.com"))
        root.addLayout(filters)

        self.status_label = QLabel("Подборка проверенных модов")
        self.status_label.setObjectName("Muted")
        self.status_label.setStyleSheet("font-size: 11px;")
        root.addWidget(self.status_label)

        self.grid = QWidget()
        self.grid_layout = FlowLayout(self.grid, margin=0, spacing=14)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_scroll.setWidget(self.grid)
        root.addWidget(self.grid_scroll, 1)

        self.empty = EmptyState(
            "Ничего не нашлось",
            "Попробуйте другой запрос или откройте подборку популярных модов.",
            "◈",
        )
        self.empty.setVisible(False)
        root.addWidget(self.empty, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.page_label = QLabel("")
        self.page_label.setObjectName("Muted")
        self.page_label.setStyleSheet("font-size: 11px;")
        bottom.addWidget(self.page_label)
        bottom.addStretch(1)
        self.more_button = QPushButton("Показать ещё")
        self.more_button.setObjectName("Secondary")
        self.more_button.setCursor(Qt.PointingHandCursor)
        self.more_button.setVisible(False)
        self.more_button.clicked.connect(self.load_more_requested.emit)
        bottom.addWidget(self.more_button)
        root.addLayout(bottom)

    def _chip(self, text: str, mode: str, layout: QHBoxLayout) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Chip")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: self.sort_requested.emit(mode))
        self._group.addButton(button)
        layout.addWidget(button)
        return button

    def _submit_search(self) -> None:
        query = self.search_edit.text().strip()
        self._search = query
        if query:
            for button in self._group.buttons():
                self._group.setExclusive(False)
                button.setChecked(False)
                self._group.setExclusive(True)
            self.search_requested.emit(query)
        else:
            self.chip_catalog.setChecked(True)
            self.sort_requested.emit("catalog")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "catalog":
            self.chip_catalog.setChecked(True)
        elif mode == "popular":
            self.chip_popular.setChecked(True)
        elif mode == "latest":
            self.chip_new.setChecked(True)
        else:
            for button in self._group.buttons():
                self._group.setExclusive(False)
                button.setChecked(False)
                self._group.setExclusive(True)

    def set_listings(self, listings: list[ModListing], *, append: bool = False) -> None:
        if not append:
            self._clear()
        for listing in listings:
            card = ModCard(listing)
            card.clicked.connect(self.mod_selected.emit)
            self.grid_layout.addWidget(card)
            self._cards.append(card)
            self._cards[-1].show()
        self.grid.updateGeometry()
        self.empty.setVisible(not self._cards)
        self.grid_scroll.setVisible(bool(self._cards))
        self.page_label.setText(f"Показано модов: {len(self._cards)}")

    def _clear(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        self.grid.updateGeometry()

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-size: 11px; color: {'#FF6273' if error else '#7E8D7A'};"
        )

    def set_loading(self, loading: bool, text: str = "") -> None:
        self.more_button.setVisible(not loading and bool(self._cards))
        self.more_button.setEnabled(not loading)
        if loading and text:
            self.status_label.setText(text)
            self.status_label.setStyleSheet("font-size: 11px; color: #7E8D7A;")

    def set_page_hint(self, text: str) -> None:
        self.page_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        self.search_edit.setEnabled(not busy)
        for button in self._group.buttons():
            button.setEnabled(not busy)
        if busy:
            self.more_button.setEnabled(False)
        else:
            self.more_button.setEnabled(True)

    def set_more_visible(self, visible: bool) -> None:
        self.more_button.setVisible(visible)
