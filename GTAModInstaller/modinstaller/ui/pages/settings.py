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

from ...config import APP_DIR, BACKUPS_DIR, DOWNLOADS_DIR
from ...game import GameInfo
from ..theme import set_pill
from ..widgets import ElidedLabel, caption, hline


def _card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Panel")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(8)
    layout.addWidget(caption(title))
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        layout.addWidget(note)
    return card, layout


class SettingsPage(QWidget):
    change_game = Signal()
    detect_game = Signal()
    clear_dependencies = Signal()
    clear_images = Signal()
    open_data = Signal()
    about_requested = Signal()

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)
        area.setWidget(body)
        root.addWidget(area, 1)

        game_card, game_layout = _card(
            "Папка игры", "Установщик определяет версию игры и проверяет права на запись."
        )
        game_row = QHBoxLayout()
        game_row.setSpacing(12)
        self.game_dot = QFrame()
        self.game_dot.setObjectName("GameDot")
        self.game_dot.setFixedSize(10, 10)
        game_row.addWidget(self.game_dot, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.game_title = QLabel("Игра не выбрана")
        self.game_title.setObjectName("CardTitle")
        title_row.addWidget(self.game_title)
        self.game_pill = QLabel("")
        self.game_pill.setVisible(False)
        title_row.addWidget(self.game_pill)
        title_row.addStretch(1)
        text.addLayout(title_row)
        self.game_path = ElidedLabel("Укажите папку с GTA5.exe", Qt.ElideMiddle)
        self.game_path.setObjectName("Muted")
        self.game_path.setStyleSheet("font-size: 11px;")
        text.addWidget(self.game_path)
        game_row.addLayout(text, 1)
        change = QPushButton("Сменить")
        change.setObjectName("Secondary")
        change.setCursor(Qt.PointingHandCursor)
        change.clicked.connect(self.change_game.emit)
        game_row.addWidget(change)
        detect = QPushButton("Найти игру")
        detect.setObjectName("Ghost")
        detect.setCursor(Qt.PointingHandCursor)
        detect.clicked.connect(self.detect_game.emit)
        game_row.addWidget(detect)
        game_layout.addLayout(game_row)
        layout.addWidget(game_card)

        behavior_card, behavior_layout = _card(
            "Поведение установщика", "Настройки применяются к следующим установкам."
        )
        self.auto_deps = QCheckBox("Автоматически предлагать установку зависимостей")
        self.backups = QCheckBox("Сохранять резервные копии заменяемых файлов")
        self.check_running = QCheckBox("Не устанавливать, пока запущена GTA V")
        for box, field in (
            (self.auto_deps, "auto_install_deps"),
            (self.backups, "make_backups"),
            (self.check_running, "check_game_running"),
        ):
            box.setChecked(bool(getattr(config, field)))
            box.toggled.connect(lambda checked, name=field: self._store(name, checked))
            behavior_layout.addWidget(box)
        behavior_layout.addWidget(hline())
        backup_note = QLabel(f"Резервные копии: {BACKUPS_DIR}")
        backup_note.setObjectName("Muted")
        backup_note.setWordWrap(True)
        backup_note.setStyleSheet("font-size: 10px;")
        behavior_layout.addWidget(backup_note)
        layout.addWidget(behavior_card)

        data_card, data_layout = _card(
            "Данные и кэш",
            "Загруженные архивы зависимостей кэшируются на 21 день. Изображения модов хранятся отдельно.",
        )
        self.data_path = QLabel(str(APP_DIR))
        self.data_path.setObjectName("Muted")
        self.data_path.setWordWrap(True)
        self.data_path.setStyleSheet("font-size: 11px;")
        data_layout.addWidget(self.data_path)
        downloads_note = QLabel(f"Загрузки модов: {DOWNLOADS_DIR}")
        downloads_note.setObjectName("Muted")
        downloads_note.setWordWrap(True)
        downloads_note.setStyleSheet("font-size: 10px;")
        data_layout.addWidget(downloads_note)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        clear_deps = QPushButton("Очистить кэш зависимостей")
        clear_deps.setObjectName("Secondary")
        clear_deps.setCursor(Qt.PointingHandCursor)
        clear_deps.clicked.connect(self.clear_dependencies.emit)
        buttons.addWidget(clear_deps)
        clear_images = QPushButton("Очистить изображения")
        clear_images.setObjectName("Ghost")
        clear_images.setCursor(Qt.PointingHandCursor)
        clear_images.clicked.connect(self.clear_images.emit)
        buttons.addWidget(clear_images)
        open_data = QPushButton("Открыть папку данных")
        open_data.setObjectName("Ghost")
        open_data.setCursor(Qt.PointingHandCursor)
        open_data.clicked.connect(self.open_data.emit)
        buttons.addWidget(open_data)
        buttons.addStretch(1)
        data_layout.addLayout(buttons)
        layout.addWidget(data_card)

        about_card, about_layout = _card("О приложении")
        about_row = QHBoxLayout()
        about_row.setSpacing(10)
        info = QLabel(
            "GTA V Mod Installer распаковывает архивы, раскладывает файлы по папкам игры "
            "и управляет зависимостями. Моды никогда не запускаются автоматически."
        )
        info.setObjectName("Dim")
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 11px;")
        about_row.addWidget(info, 1)
        about = QPushButton("Подробнее")
        about.setObjectName("Secondary")
        about.setCursor(Qt.PointingHandCursor)
        about.clicked.connect(self.about_requested.emit)
        about_row.addWidget(about)
        about_layout.addLayout(about_row)
        layout.addWidget(about_card)

        layout.addStretch(1)

    def _store(self, field: str, value: bool) -> None:
        setattr(self.config, field, bool(value))
        self.config.save()

    def set_game(self, game: GameInfo | None) -> None:
        if game is None:
            self.game_dot.setProperty("state", "bad")
            self.game_title.setText("Игра не выбрана")
            self.game_path.setText("Укажите папку с GTA5.exe")
            self.game_path.setToolTip("")
            self.game_pill.setVisible(False)
        else:
            self.game_dot.setProperty("state", "ok")
            self.game_title.setText(game.label)
            details = [str(game.path)]
            if game.version:
                details.append(f"v{game.version}")
            if not game.writable:
                details.append("нет прав на запись")
            self.game_path.setText("  ·  ".join(details))
            self.game_path.setToolTip(str(game.path))
            if game.version:
                self.game_pill.setText(game.version)
                set_pill(self.game_pill, "accent")
                self.game_pill.setVisible(True)
            else:
                self.game_pill.setVisible(False)
        self.game_dot.style().unpolish(self.game_dot)
        self.game_dot.style().polish(self.game_dot)
