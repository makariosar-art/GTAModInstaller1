from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...deps_defs import DEP_BY_KEY, DEP_GROUP_ORDER, DEPS, DepDef
from ...dependencies import DepState
from ..theme import set_pill
from ..widgets import InfoBanner, caption


class _DepRow(QFrame):
    def __init__(self, dep: DepDef, parent=None) -> None:
        super().__init__(parent)
        self.dep = dep
        self._available = False
        self.setObjectName("DepRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 13, 11)
        layout.setSpacing(12)

        badge = QLabel("◆")
        badge.setObjectName("DepBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(34, 34)
        layout.addWidget(badge, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name = QLabel(dep.name)
        name.setStyleSheet("font-size: 13px; font-weight: 600;")
        name_row.addWidget(name)
        self.version = QLabel("")
        self.version.setObjectName("Muted")
        self.version.setStyleSheet("font-size: 10px;")
        name_row.addWidget(self.version)
        name_row.addStretch(1)
        text.addLayout(name_row)
        subtitle = QLabel(dep.subtitle)
        subtitle.setObjectName("Dim")
        subtitle.setStyleSheet("font-size: 11px;")
        text.addWidget(subtitle)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setStyleSheet("font-size: 11px;")
        self.detail.setWordWrap(True)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(4)
        self.pill = QLabel("")
        self.pill.setAlignment(Qt.AlignCenter)
        actions.addWidget(self.pill, 0, Qt.AlignRight)
        self.checkbox = QCheckBox("Выбрать")
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.setVisible(dep.auto)
        actions.addWidget(self.checkbox, 0, Qt.AlignRight)
        self.site_button = QPushButton("Открыть сайт")
        self.site_button.setObjectName("Link")
        self.site_button.setCursor(Qt.PointingHandCursor)
        self.site_button.setVisible(not dep.auto)
        actions.addWidget(self.site_button, 0, Qt.AlignRight)
        layout.addLayout(actions)

    def apply(self, state: DepState | None) -> None:
        if state is None:
            state = DepState(self.dep.key, self.dep.name, False, "Нет данных.", auto=self.dep.auto)
        if state.installed is True:
            set_pill(self.pill, "ok")
            self.pill.setText("УСТАНОВЛЕНО")
        elif state.installed is False:
            set_pill(self.pill, "warn")
            self.pill.setText("НЕТ")
        else:
            set_pill(self.pill, "muted")
            self.pill.setText("ВРУЧНУЮ")
        self.detail.setText(state.detail or self.dep.description)
        self.detail.setToolTip(self.dep.description)
        self.version.setText(f"v{state.version}" if state.version else "")
        available = self.dep.auto and state.installed is False
        self._available = available
        self.checkbox.setVisible(self.dep.auto)
        self.checkbox.setEnabled(available)
        self.checkbox.setChecked(available)

    @property
    def available(self) -> bool:
        return self._available


class DependenciesPage(QWidget):
    install_requested = Signal(list)
    refresh_requested = Signal()
    site_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[str, _DepRow] = {}
        self._states: dict[str, DepState] = {}
        self._has_game = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.banner = InfoBanner(
            "Проверка компонентов",
            "Установщик проверяет папку игры: Script Hook V, SHVDN, UI-библиотеки, загрузчики и патчи.",
            kind="info",
        )
        root.addWidget(self.banner)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(8)
        for group in DEP_GROUP_ORDER:
            group_deps = [dep for dep in DEPS if dep.group == group]
            if not group_deps:
                continue
            body_layout.addWidget(caption(group))
            for dep in group_deps:
                row = _DepRow(dep)
                row.site_button.clicked.connect(lambda _=False, url=dep.url: self.site_requested.emit(url))
                row.checkbox.toggled.connect(self._update_footer)
                body_layout.addWidget(row)
                self._rows[dep.key] = row
        body_layout.addStretch(1)
        area.setWidget(body)
        root.addWidget(area, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.selection_label = QLabel("Папка игры не выбрана.")
        self.selection_label.setObjectName("Muted")
        self.selection_label.setStyleSheet("font-size: 11px;")
        footer.addWidget(self.selection_label, 1)
        refresh = QPushButton("Проверить снова")
        refresh.setObjectName("Secondary")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.refresh_requested.emit)
        footer.addWidget(refresh)
        self.install_button = QPushButton("Установить выбранные")
        self.install_button.setObjectName("Primary")
        self.install_button.setCursor(Qt.PointingHandCursor)
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install)
        footer.addWidget(self.install_button)
        root.addLayout(footer)

        note = QLabel(
            "Lenny's Mod Loader, OpenIV и Gameconfig устанавливаются вручную: "
            "откройте сайт компонента и следуйте инструкции автора."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 10px;")
        root.addWidget(note)

    def set_states(self, states: dict[str, DepState], has_game: bool) -> None:
        self._states = states
        self._has_game = has_game
        for key, row in self._rows.items():
            row.apply(states.get(key))
        if has_game:
            missing = self.missing_auto_count()
            self.banner.set_text(
                f"Папка игры проверена. Не хватает автоматических компонентов: {missing}."
                if missing
                else "Все автоматические компоненты на месте."
            )
        else:
            self.banner.set_text("Сначала укажите папку GTA V — без неё проверка невозможна.")
        self._update_footer()

    def missing_auto_count(self) -> int:
        return sum(
            1
            for key, state in self._states.items()
            if state.auto and state.installed is False
        )

    def selected_keys(self) -> list[str]:
        keys: list[str] = []
        for key, row in self._rows.items():
            dep = DEP_BY_KEY.get(key)
            if dep is None or not dep.auto:
                continue
            if row.checkbox.isChecked():
                state = self._states.get(key)
                if state is None or state.installed is not True:
                    keys.append(key)
        return keys

    def _update_footer(self, *_args) -> None:
        keys = self.selected_keys()
        if not self._has_game:
            self.selection_label.setText("Папка игры не выбрана.")
        elif keys:
            names = ", ".join(DEP_BY_KEY[key].name for key in keys[:3] if key in DEP_BY_KEY)
            if len(keys) > 3:
                names += f" и ещё {len(keys) - 3}"
            self.selection_label.setText(f"Выбрано: {names}")
        else:
            missing = self.missing_auto_count()
            self.selection_label.setText(
                "Все автоматические компоненты установлены."
                if not missing and self._states
                else "Отметьте компоненты для установки."
            )
        self.install_button.setEnabled(bool(keys) and self._has_game)

    def _install(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.install_requested.emit(keys)

    def set_busy(self, busy: bool) -> None:
        for row in self._rows.values():
            row.checkbox.setEnabled(not busy and row.available)
        if busy:
            self.install_button.setEnabled(False)
        else:
            self._update_footer()
