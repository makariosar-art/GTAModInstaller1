from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..analyzer import Analyzer, apply_game_context, dependencies_missing
from ..applog import get_logger, log_file_path, setup_logging
from ..config import APP_DIR, BACKUPS_DIR, Config, purge_cached_images, purge_staging, resource_path
from ..deps_defs import DEP_BY_KEY
from ..dependencies import clear_dependency_cache, dependency_states
from ..game import GameInfo, build_game_info, detect_games
from ..installer import (
    InstallError,
    delete_backup,
    list_backups,
    list_installed_mods,
    remove_installed_mod,
    rollback_manifest,
)
from ..site import ModListing, SiteClient, load_catalog
from ..staging import prepare_source
from ..utils import (
    clean_name_from_file,
    human_size,
    open_folder,
    open_url,
    plural_ru,
    reveal_path,
)
from ..workflow import install_dependencies as run_dependency_install
from ..workflow import install_workflow, pending_auto_dependencies
from .dialogs import (
    AboutDialog,
    GamePickerDialog,
    InstallResultDialog,
    ModDialog,
    NoticeDialog,
    PlanDialog,
    ReadmeDialog,
    confirm,
    show_error,
)
from .image_loader import prefetch_images
from .models import SourceState
from .pages.backups import BackupsPage
from .pages.dependencies import DependenciesPage
from .pages.install import InstallPage
from .pages.journal import JournalPage
from .pages.mods import InstalledModsPage
from .pages.online import OnlinePage
from .pages.settings import SettingsPage
from .theme import COLORS, apply_dark_titlebar
from .widgets import ElidedLabel
from .workers import Task, cancelled_exceptions

log = get_logger("ui")

PAGE_META: dict[str, tuple[str, str]] = {
    "install": (
        "Установка модов",
        "Перетащите архив или папку — установщик распакует и разложит файлы по папкам GTA V.",
    ),
    "mods": (
        "Мои моды",
        "Всё, что установлено через установщик: файлы, размер и удаление мода из игры.",
    ),
    "online": (
        "Онлайн-моды",
        "Каталог GTA5-Mods.com: выберите мод, а установщик скачает его и зависимости.",
    ),
    "deps": (
        "Зависимости",
        "Script Hook V, SHVDN и другие компоненты, без которых моды не запускаются.",
    ),
    "backups": (
        "Резервные копии",
        "История установок и полный откат изменений в папке игры.",
    ),
    "log": (
        "Журнал",
        "Скачивания, анализ, установка и откаты — всё в одном месте.",
    ),
    "settings": (
        "Настройки",
        "Игра, поведение установщика, кэш и данные приложения.",
    ),
}

NAV_ITEMS = (
    ("install", "Установка"),
    ("mods", "Мои моды"),
    ("online", "Онлайн-моды"),
    ("deps", "Зависимости"),
    ("backups", "Резервные копии"),
    ("log", "Журнал"),
)


def _progress_slice(context, start: int, end: int):
    span = max(1, end - start)

    def report(percent, message: str) -> None:
        if percent is None:
            context.progress(None, message)
        else:
            value = max(0, min(100, int(percent)))
            context.progress(start + int(span * value / 100), message)

    return report


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        setup_logging()
        self.config = config
        self.sources: dict[str, SourceState] = {}
        self.order: list[str] = []
        self.game: GameInfo | None = None
        self.games: list[GameInfo] = []
        self._task: Task | None = None
        self._cancel_enabled = False
        self._closing = False
        self._shutdown_done = False
        self._centered = False
        self._current_page = "install"
        self._online_mode = "catalog"
        self._online_query = ""
        self._online_page = 0
        self._online_tried = False
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle("GTA V Mod Installer")
        icon = resource_path("assets", "icon.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.setAcceptDrops(True)
        self.resize(1300, 840)
        self.setMinimumSize(1120, 700)
        purge_staging()

        self._build_ui()
        self._wire_pages()
        self._restore_geometry()
        self._initial_game()
        QTimer.singleShot(0, self._startup)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = self._build_sidebar()
        outer.addWidget(sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(24, 16, 24, 12)
        main.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(18)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        eyebrow = QLabel("GTA V MOD INSTALLER")
        eyebrow.setObjectName("Eyebrow")
        titles.addWidget(eyebrow)
        self.page_title = QLabel("Установка модов")
        self.page_title.setObjectName("PageTitle")
        titles.addWidget(self.page_title)
        self.page_subtitle = QLabel(PAGE_META["install"][1])
        self.page_subtitle.setObjectName("PageSubtitle")
        self.page_subtitle.setWordWrap(True)
        titles.addWidget(self.page_subtitle)
        header.addLayout(titles, 1)

        from .widgets import GameBar

        self.game_bar = GameBar()
        self.game_bar.change_clicked.connect(self._choose_game)
        self.game_bar.detect_clicked.connect(self._detect_game_deep)
        header.addWidget(self.game_bar, 0, Qt.AlignTop)
        main.addLayout(header)

        self.install_page = InstallPage()
        self.mods_page = InstalledModsPage()
        self.online_page = OnlinePage()
        self.deps_page = DependenciesPage()
        self.backups_page = BackupsPage()
        self.journal_page = JournalPage()
        self.settings_page = SettingsPage(self.config)
        self.pages: dict[str, QWidget] = {
            "install": self.install_page,
            "mods": self.mods_page,
            "online": self.online_page,
            "deps": self.deps_page,
            "backups": self.backups_page,
            "log": self.journal_page,
            "settings": self.settings_page,
        }
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        main.addWidget(self.stack, 1)

        self.status_strip = QFrame()
        self.status_strip.setObjectName("StatusStrip")
        self.status_strip.setFixedHeight(44)
        strip = QHBoxLayout(self.status_strip)
        strip.setContentsMargins(13, 5, 10, 5)
        strip.setSpacing(10)
        self.status_dot = QLabel("●")
        self.status_dot.setFixedWidth(12)
        self.status_dot.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px;")
        strip.addWidget(self.status_dot)
        self.status_text = ElidedLabel("Готово")
        self.status_text.setObjectName("Dim")
        self.status_text.setStyleSheet("font-size: 11px;")
        strip.addWidget(self.status_text, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(250)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        strip.addWidget(self.progress)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.setFixedHeight(26)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_task)
        strip.addWidget(self.cancel_button)
        main.addWidget(self.status_strip)

        outer.addLayout(main, 1)
        self.setCentralWidget(central)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(236)
        box = QVBoxLayout(sidebar)
        box.setContentsMargins(16, 20, 16, 16)
        box.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel("V")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(42, 42)
        brand.addWidget(mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        brand_title = QLabel("MOD INSTALLER")
        brand_title.setObjectName("BrandTitle")
        names.addWidget(brand_title)
        brand_sub = QLabel("GTA V · WINDOWS")
        brand_sub.setObjectName("BrandSub")
        names.addWidget(brand_sub)
        brand.addLayout(names)
        brand.addStretch(1)
        box.addLayout(brand)
        box.addSpacing(12)

        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)
        box.addWidget(divider)
        box.addSpacing(8)

        group_label = QLabel("РАЗДЕЛЫ")
        group_label.setObjectName("SectionCaption")
        box.addWidget(group_label)
        box.addSpacing(2)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for index, (key, label) in enumerate(NAV_ITEMS, start=1):
            button = QPushButton(f"{index:02d}    {label}")
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(38)
            button.clicked.connect(lambda _=False, page=key: self.show_page(page))
            self._nav_group.addButton(button)
            self.nav_buttons[key] = button
            box.addWidget(button)

        box.addStretch(1)

        tip = QFrame()
        tip.setObjectName("PanelAlt")
        tip_layout = QVBoxLayout(tip)
        tip_layout.setContentsMargins(11, 9, 11, 9)
        tip_layout.setSpacing(2)
        tip_title = QLabel("ЛОКАЛЬНЫЙ РЕЖИМ")
        tip_title.setObjectName("SectionCaption")
        tip_layout.addWidget(tip_title)
        tip_text = QLabel("Моды копируются только в выбранную папку игры.")
        tip_text.setObjectName("Muted")
        tip_text.setWordWrap(True)
        tip_text.setStyleSheet("font-size: 10px;")
        tip_layout.addWidget(tip_text)
        box.addWidget(tip)
        box.addSpacing(8)

        settings = QPushButton("Настройки")
        settings.setObjectName("Nav")
        settings.setCheckable(True)
        settings.setCursor(Qt.PointingHandCursor)
        settings.setFixedHeight(36)
        settings.clicked.connect(lambda: self.show_page("settings"))
        self._nav_group.addButton(settings)
        self.nav_buttons["settings"] = settings
        box.addWidget(settings)
        about = QPushButton("О программе")
        about.setObjectName("Ghost")
        about.setCursor(Qt.PointingHandCursor)
        about.setStyleSheet("text-align: left; padding: 6px 12px; font-size: 11px;")
        about.clicked.connect(self._show_about)
        box.addWidget(about)
        version = QLabel(f"v{__version__}  ·  неофициальный инструмент")
        version.setObjectName("Muted")
        version.setStyleSheet("font-size: 10px;")
        box.addWidget(version)
        return sidebar

    def _wire_pages(self) -> None:
        self.install_page.paths_dropped.connect(self._add_paths)
        self.install_page.browse_archive.connect(self._browse_archive)
        self.install_page.browse_folder.connect(self._browse_folder)
        self.install_page.install_all.connect(self._install_all)
        self.install_page.plan_requested.connect(self._install_source)
        self.install_page.remove_requested.connect(self._remove_source)
        self.install_page.retry_requested.connect(self._retry_source)
        self.install_page.readme_requested.connect(self._show_readme)
        self.install_page.clear_requested.connect(self._clear_sources)
        self.install_page.online_requested.connect(lambda: self.show_page("online"))

        self.mods_page.remove_requested.connect(self._remove_installed_mod)
        self.mods_page.refresh_requested.connect(self._refresh_mods)
        self.mods_page.backups_requested.connect(lambda: self.show_page("backups"))

        self.online_page.search_requested.connect(self._online_search)
        self.online_page.sort_requested.connect(self._online_sort)
        self.online_page.load_more_requested.connect(self._online_more)
        self.online_page.mod_selected.connect(self._open_mod)

        self.deps_page.install_requested.connect(self._install_deps)
        self.deps_page.refresh_requested.connect(self._refresh_dependencies)
        self.deps_page.site_requested.connect(open_url)

        self.backups_page.rollback_requested.connect(self._rollback_backup)
        self.backups_page.delete_requested.connect(self._delete_backup)
        self.backups_page.refresh_requested.connect(self._refresh_backups)
        self.backups_page.open_folder_requested.connect(lambda: open_folder(BACKUPS_DIR))

        self.journal_page.open_file_requested.connect(lambda: reveal_path(log_file_path()))
        self.journal_page.clear_requested.connect(self.journal_page.clear)

        self.settings_page.change_game.connect(self._choose_game)
        self.settings_page.detect_game.connect(self._detect_game_deep)
        self.settings_page.clear_dependencies.connect(self._clear_dependency_cache)
        self.settings_page.clear_images.connect(self._clear_images)
        self.settings_page.open_data.connect(lambda: open_folder(APP_DIR))
        self.settings_page.about_requested.connect(self._show_about)

        self._shortcut("Ctrl+O", self._browse_archive)
        self._shortcut("Ctrl+Shift+O", self._browse_folder)
        self._shortcut("Ctrl+1", lambda: self.show_page("install"))
        self._shortcut("Ctrl+2", lambda: self.show_page("mods"))
        self._shortcut("Ctrl+3", lambda: self.show_page("online"))
        self._shortcut("Ctrl+4", lambda: self.show_page("deps"))
        self._shortcut("Ctrl+5", lambda: self.show_page("backups"))
        self._shortcut("Ctrl+6", lambda: self.show_page("log"))
        self._shortcut("Ctrl+7", lambda: self.show_page("settings"))
        self._shortcut("F5", self._refresh_current)

    def _shortcut(self, sequence: str, slot) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.activated.connect(slot)
        self._shortcuts.append(shortcut)

    # ------------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_dark_titlebar(self)
        if not self._centered:
            self._centered = True
            if not self.config.window_geometry:
                screen = QApplication.primaryScreen()
                if screen is not None:
                    geo = screen.availableGeometry()
                    self.move(geo.center() - self.rect().center())

    def closeEvent(self, event) -> None:
        if self._task is not None:
            if not confirm(
                self,
                "Закрыть установщик?",
                "Сейчас выполняется операция. При закрытии она будет отменена, "
                "а незавершённая установка — откачена.",
                accept_text="Прервать и закрыть",
                danger=True,
                hint="Лучше дождаться завершения или нажать «Отмена» внизу окна.",
            ):
                event.ignore()
                return
            task = self._task
            self._set_status("Отмена операции…")
            task.cancel()
            task.wait(6000)
            if task.isRunning():
                NoticeDialog(
                    "Операция не завершена",
                    "Установщик пока не может безопасно прервать операцию. "
                    "Дождитесь её окончания и закройте окно снова.",
                    kind="warn",
                    parent=self,
                ).exec()
                event.ignore()
                return
            self._task = None
        self._save_geometry()
        self.config.save()
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._closing = True
        task = self._task
        if task is not None:
            task.cancel()
            try:
                task.wait(6000)
            except Exception:
                pass
            self._task = None
        for state in list(self.sources.values()):
            try:
                state.cleanup()
            except Exception:
                pass
        self.sources.clear()
        self.order.clear()
        self._save_geometry()
        self.config.save()

    def _restore_geometry(self) -> None:
        data = (self.config.window_geometry or "").strip()
        if not data:
            return
        try:
            self.restoreGeometry(QByteArray.fromBase64(data.encode("ascii")))
        except Exception:
            pass

    def _save_geometry(self) -> None:
        try:
            self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            pass

    def _startup(self) -> None:
        self._log_ui("Приложение запущено")
        if self.game is not None:
            self._log_ui(f"Игра: {self.game.label} · {self.game.path}")
        else:
            self._log_ui("Игра не найдена — укажите папку GTA V.")
        if self._current_page == "install":
            # Не сбрасывать раздел, выбранный через --page.
            self.show_page("install")
        self._refresh_backups()
        self._refresh_mods()
        self._refresh_dependencies()
        catalog = load_catalog()
        if catalog:
            self.online_page.set_listings(catalog)
            self.online_page.set_status(
                f"Подборка проверенных модов: {len(catalog)}. Популярные подтянутся с сайта."
            )
            self.online_page.set_more_visible(False)
            prefetch_images([item.image for item in catalog if item.image])
        if self.config.first_run:
            self.config.first_run = False
            self.config.save()
            game_hint = (
                f"Игра найдена: {self.game.label}."
                if self.game is not None
                else "Игра пока не найдена — укажите папку GTA V в правом верхнем углу."
            )
            NoticeDialog(
                "Добро пожаловать",
                "Перетащите ZIP, RAR, 7Z, OIV или папку с модом в окно. "
                "Установщик распакует архив, разложит файлы и предложит зависимости.\n\n"
                + game_hint,
                "Перед записью всегда открывается план установки. Файлы заменяются только после подтверждения.",
                kind="info",
                parent=self,
            ).exec()

    # -------------------------------------------------------------- game

    def _initial_game(self) -> None:
        saved = (self.config.game_path or "").strip()
        if saved:
            info = build_game_info(saved, "Сохранённый путь")
            if info is not None:
                self.game = info
        if self.game is None:
            self.games = detect_games(False)
            if self.games:
                self.game = self.games[0]
        if self.game is not None:
            self.config.game_path = str(self.game.path)
            self.config.save()
        self.game_bar.set_game(self.game)
        self.settings_page.set_game(self.game)

    def _apply_game(self, game: GameInfo) -> None:
        self.game = game
        self.config.game_path = str(game.path)
        self.config.save()
        self.game_bar.set_game(game)
        self.settings_page.set_game(game)
        self._log_ui(f"Выбрана игра: {game.label} · {game.path}")
        for state in self.sources.values():
            if state.plan is not None:
                apply_game_context([state.plan], game.path)
        self._sync_sources()
        self._refresh_dependencies()

    def _choose_game(self) -> None:
        if self._guard_busy():
            return
        games = detect_games(False)
        if self.game is not None and all(game.path != self.game.path for game in games):
            games.insert(0, self.game)
        dialog = GamePickerDialog(games, self)
        dialog.exec()
        if dialog.result() != QDialog.Accepted:
            return
        if dialog.manual_requested:
            self._pick_manual_game()
            return
        if dialog.selected is not None:
            self._apply_game(dialog.selected)

    def _pick_manual_game(self) -> None:
        start = self.config.game_path or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Папка Grand Theft Auto V", start)
        if not selected:
            return
        info = build_game_info(selected, "Вручную")
        if info is None:
            base = Path(selected)
            try:
                for child in base.iterdir():
                    if child.is_dir():
                        candidate = build_game_info(child, "Вручную")
                        if candidate is not None:
                            info = candidate
                            break
            except OSError:
                pass
        if info is None:
            show_error(
                self,
                "GTA V не найдена",
                "В выбранной папке нет GTA5.exe или GTA5_Enhanced.exe.",
                hint="Выберите папку, где лежит исполняемый файл игры, например "
                "…\\Grand Theft Auto V\\GTA5.exe.",
            )
            return
        self._apply_game(info)

    def _detect_game_deep(self) -> None:
        if self._guard_busy():
            return

        def work(context):
            return detect_games(True, context.progress)

        def done(games: list[GameInfo]) -> None:
            if not games:
                NoticeDialog(
                    "Игра не найдена",
                    "Автоматический поиск не нашёл GTA5.exe на дисках.",
                    "Укажите папку игры вручную кнопкой «Сменить».",
                    kind="warn",
                    parent=self,
                ).exec()
                return
            if len(games) == 1:
                self._apply_game(games[0])
                return
            self.games = games
            dialog = GamePickerDialog(games, self)
            dialog.exec()
            if dialog.result() != QDialog.Accepted:
                return
            if dialog.manual_requested:
                self._pick_manual_game()
            elif dialog.selected is not None:
                self._apply_game(dialog.selected)

        if not self._start_task(
            "Поиск игры",
            work,
            done,
            busy_message="Глубокий поиск GTA V на дисках… это может занять до 20 секунд.",
        ):
            return

    def use_game_override(self, path: str) -> None:
        info = build_game_info(path, "Параметр запуска")
        if info is None:
            show_error(
                self,
                "GTA V не найдена",
                f"В папке нет GTA5.exe:\n{path}",
                hint="Укажите папку игры через кнопку «Сменить» в правом верхнем углу.",
            )
            return
        self._apply_game(info)

    def _require_game(self) -> bool:
        if self.game is not None:
            return True
        self._choose_game()
        if self.game is not None:
            return True
        show_error(
            self,
            "Сначала выберите игру",
            "Для установки нужна папка GTA V с файлом GTA5.exe.",
            hint="Нажмите «Указать» в правом верхнем углу и выберите папку игры.",
        )
        return False

    # ------------------------------------------------------------- sources

    def _ordered_states(self) -> list[SourceState]:
        return [self.sources[key] for key in self.order if key in self.sources]

    def _sync_sources(self) -> None:
        self.install_page.sync(self._ordered_states())

    def add_paths_later(self, paths: list[str]) -> None:
        QTimer.singleShot(0, lambda: self._add_paths(paths))

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            try:
                path = Path(raw).expanduser()
            except (OSError, ValueError):
                continue
            if not path.exists():
                self._log_ui(f"Пропущен недоступный путь: {raw}")
                continue
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved.is_dir() and any(
                (resolved / exe).is_file() for exe in ("GTA5.exe", "GTA5_Enhanced.exe")
            ):
                show_error(
                    self,
                    "Это папка игры",
                    "Нельзя установить игру как мод. Перетащите архив или папку с модом.",
                    hint="Папка GTA V выбирается отдельно — кнопкой «Сменить» вверху окна.",
                )
                continue
            key = os.path.normcase(str(resolved))
            if any(
                state.path is not None and os.path.normcase(str(state.path)) == key
                for state in self.sources.values()
            ):
                continue
            title = clean_name_from_file(path.name)
            state = SourceState(
                path=resolved,
                title=title or path.name,
                origin="file",
                message="В очереди",
            )
            self.sources[state.key] = state
            self.order.append(state.key)
            added += 1
            self._log_ui(f"Добавлено в очередь: {path.name}")
        if added:
            self.show_page("install")
            self._sync_sources()
            self._pump_queue()

    def _browse_archive(self) -> None:
        start = self.config.last_browse_dir or str(Path.home())
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Выберите архив мода",
            start,
            "Архивы модов (*.zip *.rar *.7z *.oiv *.tar *.gz *.tgz);;Все файлы (*.*)",
        )
        if not filename:
            return
        self.config.last_browse_dir = str(Path(filename).parent)
        self.config.save()
        self._add_paths([filename])

    def _browse_folder(self) -> None:
        start = self.config.last_browse_dir or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку с модом", start)
        if not selected:
            return
        self.config.last_browse_dir = selected
        self.config.save()
        self._add_paths([selected])

    def _remove_source(self, key: str) -> None:
        if self._guard_busy():
            return
        state = self.sources.pop(key, None)
        if state is None:
            return
        try:
            self.order.remove(key)
        except ValueError:
            pass
        state.cleanup()
        self._sync_sources()
        self._log_ui(f"Убрано из очереди: {state.title}")

    def _retry_source(self, key: str) -> None:
        if self._guard_busy():
            return
        state = self.sources.get(key)
        if state is None:
            return
        state.cleanup()
        state.status = "queued"
        state.message = "В очереди"
        state.error = ""
        state.hint = ""
        self._sync_sources()
        self._pump_queue()

    def _clear_sources(self) -> None:
        if self._guard_busy() or not self.sources:
            return
        for state in self.sources.values():
            state.cleanup()
        self.sources.clear()
        self.order.clear()
        self._sync_sources()
        self._log_ui("Очередь очищена.")

    def _show_readme(self, key: str) -> None:
        state = self.sources.get(key)
        if state is None or state.plan is None or not state.plan.readmes:
            NoticeDialog("Инструкция", "В этом моде нет текстовой инструкции.", kind="info", parent=self).exec()
            return
        ReadmeDialog(state.title, state.plan.readmes, self).exec()

    # ------------------------------------------------------------ analysis

    def _pump_queue(self) -> None:
        if self._task is not None or self._closing:
            return
        for key in self.order:
            state = self.sources.get(key)
            if state is not None and state.status == "queued":
                self._analyze_source(state)
                return

    def _analyze_source(self, state: SourceState) -> None:
        state.status = "preparing"
        state.message = "Подготовка…"
        state.error = ""
        state.hint = ""
        self._sync_sources()
        game_path = self.game.path if self.game is not None else None
        path = state.path
        meta = dict(state.meta)
        key = state.key

        def work(context):
            source = None
            try:
                context.progress(None, f"Распаковка: {path.name}")
                source = prepare_source(path, context.progress, meta=meta)
                context.progress(None, "Анализ файлов мода…")
                plan = Analyzer(game_path).analyze(source, context.progress)
                return source, plan
            except Exception:
                if source is not None:
                    source.cleanup()
                raise

        def done(result) -> None:
            source, plan = result
            state_now = self.sources.get(key)
            if state_now is None:
                source.cleanup()
                return
            state_now.source = source
            state_now.plan = plan
            if plan.installable:
                state_now.status = "ready"
                state_now.message = "Готов к установке"
                self._log_ui(
                    f"Анализ: {state_now.title} · файлов: {len(plan.enabled_items)}, "
                    f"зависимостей: {len(plan.deps)}"
                )
            else:
                state_now.status = "error"
                state_now.error = "В моде нет файлов для автоматической установки."
                state_now.hint = (
                    "Возможно, мод ставится вручную через OpenIV или содержит только документацию."
                )
                source.cleanup()
                state_now.source = None
                self._log_ui(f"Анализ: {state_now.title} — нет файлов для установки.")
            self._sync_sources()

        def failed(exc, message, hint) -> None:
            state_now = self.sources.get(key)
            if state_now is None:
                return
            state_now.status = "error"
            state_now.error = message
            state_now.hint = hint
            state_now.source = None
            state_now.plan = None
            self._sync_sources()
            self._log_ui(f"Ошибка анализа «{state_now.title}»: {message}")

        self._start_task(
            "Анализ мода",
            work,
            done,
            on_fail=failed,
            busy_message=f"Анализ: {state.title}",
        )

    # ------------------------------------------------------------- install

    def _install_all(self) -> None:
        ready = [state.key for state in self._ordered_states() if state.ready]
        self._open_plan(ready)

    def _install_source(self, key: str) -> None:
        self._open_plan([key])

    def _open_plan(self, keys: list[str]) -> None:
        if self._guard_busy():
            return
        if not self._require_game():
            return
        states = [
            state
            for state in self._ordered_states()
            if state.key in set(keys) and state.ready
        ]
        if not states:
            show_error(
                self,
                "Нет готовых модов",
                "Дождитесь окончания анализа или добавьте архив в очередь.",
            )
            return
        plans = [state.plan for state in states if state.plan is not None]
        apply_game_context(plans, self.game.path)
        self._sync_sources()
        if not any(plan.installable for plan in plans):
            show_error(
                self,
                "Устанавливать нечего",
                "В выбранных модах нет файлов, которые можно установить автоматически.",
            )
            return
        dialog = PlanDialog(
            plans,
            self.game,
            make_backups=self.config.make_backups,
            auto_install_deps=self.config.auto_install_deps,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        dep_keys: set[str] = set()
        if dialog.install_dependencies:
            for key, _reasons in dependencies_missing(plans).items():
                dep = DEP_BY_KEY.get(key)
                if dep is not None and dep.auto:
                    dep_keys.add(key)
        self._install_plans(plans, [state.key for state in states], dep_keys)

    def _install_plans(self, plans, keys: list[str], dep_keys: set[str]) -> None:
        game = self.game
        if game is None:
            return
        make_backups = self.config.make_backups
        check_running = self.config.check_game_running
        self._log_ui(
            f"Старт установки: модов {len(plans)}, файлов: "
            f"{sum(len(plan.enabled_items) for plan in plans)}."
        )

        def work(context):
            return install_workflow(
                plans,
                game.path,
                dep_keys,
                make_backups=make_backups,
                check_running=check_running,
                progress=context.progress,
                cancel=context.cancel_event,
            )

        def done(result) -> None:
            self._after_install(result, keys, plans)

        def failed(exc, message, hint) -> None:
            self._refresh_backups()
            self._refresh_dependencies()
            self._log_ui(f"Установка не выполнена: {message}")
            if isinstance(exc, cancelled_exceptions()):
                NoticeDialog(
                    "Установка отменена",
                    "Изменения откачены: файлы игры остались в прежнем состоянии.",
                    kind="info",
                    parent=self,
                ).exec()
                return
            show_error(self, "Ошибка установки", message, hint)

        self._start_task(
            "Установка модов",
            work,
            done,
            on_fail=failed,
            busy_message="Установка модов в папку игры…",
            cancelable=True,
        )

    def _after_install(self, result, keys: list[str], plans) -> None:
        install = result.install
        self._log_ui(
            f"Установлено: {install.title} · файлов {install.file_count}, "
            f"{human_size(install.total_bytes)}, замен {install.replaced}."
        )
        for name in result.dependencies.installed:
            self._log_ui(f"Зависимость установлена: {name}")
        for name, reason in result.dependencies.failed:
            self._log_ui(f"Зависимость не установлена: {name} — {reason}")
        for warning in install.warnings:
            self._log_ui(f"Предупреждение: {warning}")
        dialog = InstallResultDialog(result, plans, self.game, parent=self)
        manifest = install.manifest_path
        rollback_started = False

        def request_rollback() -> None:
            nonlocal rollback_started
            rollback_started = True
            self._later(self._rollback_manifest, manifest)

        dialog.rollback_requested.connect(request_rollback)
        dialog.backups_requested.connect(lambda: self._later(self.show_page, "backups"))
        dialog.exec()
        for key in keys:
            state = self.sources.pop(key, None)
            if state is not None:
                try:
                    self.order.remove(key)
                except ValueError:
                    pass
                state.cleanup()
        self._sync_sources()
        self._refresh_backups()
        self._refresh_mods()
        self._refresh_dependencies()
        self._set_status(
            f"Установлено файлов: {install.file_count} · {human_size(install.total_bytes)}"
        )
        if not rollback_started and self.game is not None:
            pending = pending_auto_dependencies(
                plans,
                self.game.path,
                installed_names=result.dependencies.installed,
            )
            if pending:
                names = ", ".join(DEP_BY_KEY[key].name for key in pending if key in DEP_BY_KEY)
                self._log_ui(f"Не хватает зависимостей: {names}.")
                self._later(self._install_deps, pending, True)

    def _install_deps(self, keys: list[str], after_install: bool = False) -> None:
        if self._guard_busy():
            return
        if not keys:
            return
        if not self._require_game():
            return
        deps = [DEP_BY_KEY[key] for key in keys if key in DEP_BY_KEY]
        listing = "\n".join(f"•  {dep.name} — {dep.subtitle}" for dep in deps)
        intro = (
            "Мод установлен. Для его работы не хватает зависимостей:"
            if after_install
            else "Будут скачаны и установлены:"
        )
        if not confirm(
            self,
            "Установить зависимости мода?" if after_install else "Установить зависимости?",
            f"{intro}\n\n{listing}",
            accept_text="Скачать и установить",
            hint="Источники: сайты авторов и GitHub. Игра должна быть закрыта. "
            "Файлы будут защищены резервной копией.",
        ):
            return
        game = self.game
        if game is None:
            return
        make_backups = self.config.make_backups
        check_running = self.config.check_game_running
        selected = list(keys)
        self._log_ui("Старт установки зависимостей: " + ", ".join(dep.name for dep in deps))

        def work(context):
            return run_dependency_install(
                selected,
                game.path,
                make_backups=make_backups,
                check_running=check_running,
                progress=context.progress,
                cancel=context.cancel_event,
            )

        def done(result) -> None:
            self._after_dependencies(result)

        def failed(exc, message, hint) -> None:
            self._refresh_backups()
            self._refresh_dependencies()
            self._log_ui(f"Зависимости не установлены: {message}")
            if isinstance(exc, cancelled_exceptions()):
                NoticeDialog(
                    "Установка отменена",
                    "Изменения откачены.",
                    kind="info",
                    parent=self,
                ).exec()
                return
            show_error(self, "Ошибка установки зависимостей", message, hint)

        self._start_task(
            "Установка зависимостей",
            work,
            done,
            on_fail=failed,
            busy_message="Скачивание и установка зависимостей…",
            cancelable=True,
        )

    def _after_dependencies(self, result) -> None:
        install = result.install
        self._log_ui(
            f"Зависимости: файлов {install.file_count}, {human_size(install.total_bytes)}."
        )
        for name in result.dependencies.installed:
            self._log_ui(f"Установлено: {name}")
        dialog = InstallResultDialog(
            result,
            [],
            self.game,
            dependency_only=True,
            parent=self,
        )
        manifest = install.manifest_path
        dialog.rollback_requested.connect(lambda: self._later(self._rollback_manifest, manifest))
        dialog.backups_requested.connect(lambda: self._later(self.show_page, "backups"))
        dialog.exec()
        self._refresh_backups()
        self._refresh_mods()
        self._refresh_dependencies()
        self._set_status(f"Зависимости установлены · файлов: {install.file_count}")

    # ------------------------------------------------------------- rollback

    def _rollback_backup(self, entry) -> None:
        self._rollback_manifest(entry.manifest_path)

    def _rollback_manifest(self, manifest_path) -> None:
        if self._guard_busy():
            return
        if not confirm(
            self,
            "Откатить установку?",
            "Установщик вернёт заменённые файлы из резервной копии и удалит созданные им файлы.",
            accept_text="Откатить",
            danger=True,
            hint="Игра должна быть закрыта. Откатить можно только один раз.",
        ):
            return
        path = Path(manifest_path)
        self._log_ui(f"Откат установки: {path.parent.name}")

        def work(context):
            return rollback_manifest(
                path,
                progress=context.progress,
                cancel=context.cancel_event,
            )

        def done(result) -> None:
            self._refresh_backups()
            self._refresh_mods()
            self._refresh_dependencies()
            self._log_ui(
                f"Откат завершён: восстановлено {result.restored}, "
                f"удалено {result.removed}, ошибок {result.failed}."
            )
            if result.failed:
                NoticeDialog(
                    "Откат завершён с ошибками",
                    f"Восстановлено файлов: {result.restored}, удалено: {result.removed}, "
                    f"ошибок: {result.failed}.",
                    "\n".join(result.warnings[:6]) or "Проверьте права доступа к папке игры.",
                    kind="warn",
                    parent=self,
                ).exec()
            else:
                NoticeDialog(
                    "Откат завершён",
                    f"Восстановлено файлов: {result.restored}. Удалено созданных: {result.removed}.",
                    kind="success",
                    parent=self,
                ).exec()

        def failed(exc, message, hint) -> None:
            self._refresh_backups()
            self._refresh_mods()
            self._refresh_dependencies()
            self._log_ui(f"Откат не выполнен: {message}")
            if isinstance(exc, cancelled_exceptions()):
                NoticeDialog("Откат отменён", "Файлы игры не изменены.", kind="info", parent=self).exec()
                return
            show_error(self, "Ошибка отката", message, hint)

        self._start_task(
            "Откат установки",
            work,
            done,
            on_fail=failed,
            busy_message="Откат изменений в папке игры…",
            cancelable=True,
        )

    def _remove_installed_mod(self, mod) -> None:
        if self._guard_busy():
            return
        hints = ["Игра должна быть закрыта."]
        if mod.dependency:
            hints.append(
                "Этот компонент нужен другим модам — после удаления они могут перестать работать."
            )
        if mod.unprotected_count:
            hints.append(
                f"У {mod.unprotected_count} "
                f"{plural_ru(mod.unprotected_count, 'заменённого файла', 'заменённых файлов', 'заменённых файлов')} "
                "нет резервной копии — они останутся в игре."
            )
        if not confirm(
            self,
            "Удалить мод?",
            f"«{mod.title}» будет удалён из папки игры. "
            f"Файлов: {mod.file_count} · {human_size(mod.size)}.",
            accept_text="Удалить мод",
            danger=True,
            hint=" ".join(hints),
        ):
            return
        path = Path(mod.manifest_path)
        title = mod.title
        self._log_ui(f"Удаление мода: {title} · сессия {mod.session_id}")

        def work(context):
            return remove_installed_mod(
                path,
                title,
                progress=context.progress,
                cancel=context.cancel_event,
            )

        def done(result) -> None:
            self._refresh_mods()
            self._refresh_backups()
            self._refresh_dependencies()
            warnings = "\n".join(result.warnings[:8])
            self._log_ui(
                f"Удаление мода «{title}»: восстановлено {result.restored}, "
                f"удалено {result.removed}, очищено {result.cleaned}, проблем {result.failed}."
            )
            if result.restored or result.removed or result.cleaned:
                partial = bool(result.failed or result.warnings)
                summary = (
                    f"Восстановлено файлов: {result.restored}, удалено: {result.removed}"
                    + (f", уже отсутствовало: {result.cleaned}" if result.cleaned else "")
                    + (f", проблем: {result.failed}." if result.failed else ".")
                )
                NoticeDialog(
                    "Мод удалён частично" if partial else "Мод удалён",
                    summary,
                    warnings
                    or "Резервные копии сессии остались во вкладке «Резервные копии».",
                    kind="warn" if partial else "success",
                    parent=self,
                ).exec()
                return
            NoticeDialog(
                "Мод не удалён",
                "Установщик не изменил файлы игры.",
                warnings or "Проверьте права доступа к папке игры.",
                kind="warn",
                parent=self,
            ).exec()

        def failed(exc, message, hint) -> None:
            self._refresh_mods()
            self._refresh_backups()
            self._refresh_dependencies()
            self._log_ui(f"Не удалось удалить мод «{title}»: {message}")
            if isinstance(exc, cancelled_exceptions()):
                NoticeDialog(
                    "Удаление отменено",
                    "Файлы игры не изменены.",
                    kind="info",
                    parent=self,
                ).exec()
                return
            show_error(self, "Ошибка удаления мода", message, hint)

        self._start_task(
            "Удаление мода",
            work,
            done,
            on_fail=failed,
            busy_message="Удаление файлов мода из игры…",
            cancelable=True,
        )

    def _delete_backup(self, entry) -> None:
        if self._guard_busy():
            return
        if not confirm(
            self,
            "Удалить резервную копию?",
            f"Копия «{entry.title}» от {entry.date_label} будет удалена. "
            "Откатить эту установку станет невозможно.",
            accept_text="Удалить",
            danger=True,
        ):
            return
        try:
            delete_backup(entry.manifest_path)
        except (InstallError, OSError) as exc:
            show_error(self, "Не удалось удалить копию", str(exc))
            return
        self._refresh_backups()
        self._log_ui(f"Резервная копия удалена: {entry.title}")

    # ---------------------------------------------------------------- online

    def _ensure_online_live(self) -> None:
        if self._online_tried or self._task is not None:
            return
        self._online_tried = True

        def work(context):
            client = SiteClient()
            try:
                context.progress(None, "Загрузка популярных модов с GTA5-Mods.com…")
                return client.list_mods("popular", 1)
            finally:
                client.close()

        def done(items: list[ModListing]) -> None:
            if not items:
                self.online_page.set_status("Каталог сайта пуст. Показана локальная подборка.")
                return
            self._online_mode = "popular"
            self._online_query = ""
            self._online_page = 1
            self.online_page.set_mode("popular")
            self.online_page.set_listings(items)
            self.online_page.set_status(f"Популярные моды GTA5-Mods.com: {len(items)}")
            self.online_page.set_more_visible(len(items) >= 10)
            prefetch_images([item.image for item in items if item.image])
            self._log_ui(f"Загружен онлайн-каталог: {len(items)} модов.")

        def failed(_exc, message, _hint) -> None:
            self.online_page.set_status(
                f"Не удалось загрузить сайт: {message}. Показана локальная подборка.",
                error=True,
            )

        self._start_task(
            "Каталог модов",
            work,
            done,
            on_fail=failed,
            busy_message="Загрузка каталога GTA5-Mods.com…",
        )

    def _online_sort(self, mode: str) -> None:
        if mode == "catalog":
            catalog = load_catalog()
            self._online_mode = "catalog"
            self._online_query = ""
            self._online_page = 0
            self.online_page.set_mode("catalog")
            self.online_page.set_listings(catalog)
            self.online_page.set_status(
                f"Подборка проверенных модов: {len(catalog)}"
                if catalog
                else "Локальная подборка пуста."
            )
            self.online_page.set_more_visible(False)
            prefetch_images([item.image for item in catalog if item.image])
            return
        if self._guard_busy():
            return
        self._load_online(mode, "", 1, append=False)

    def _online_search(self, query: str) -> None:
        if self._guard_busy():
            return
        query = query.strip()
        if not query:
            self._online_sort("catalog")
            return
        self._load_online("search", query, 1, append=False)

    def _online_more(self) -> None:
        if self._guard_busy():
            return
        if self._online_mode == "catalog":
            return
        self._load_online(
            self._online_mode,
            self._online_query,
            self._online_page + 1,
            append=True,
        )

    def _load_online(self, mode: str, query: str, page: int, *, append: bool) -> None:
        self._online_mode = mode
        self._online_query = query
        self._online_page = page
        if mode == "search":
            self.online_page.set_mode("search")
        else:
            self.online_page.set_mode(mode)
        self.online_page.set_loading(True, "Загрузка с GTA5-Mods.com…")

        def work(context):
            client = SiteClient()
            try:
                context.progress(None, "Загрузка каталога GTA5-Mods.com…")
                if mode == "search":
                    return client.search_mods(query, page)
                return client.list_mods("latest" if mode == "latest" else "popular", page)
            finally:
                client.close()

        def done(items: list[ModListing]) -> None:
            self.online_page.set_loading(False)
            self.online_page.set_listings(items, append=append)
            label = (
                f"Поиск «{query}»"
                if mode == "search"
                else "Новые моды"
                if mode == "latest"
                else "Популярные моды"
            )
            if not items and not append:
                self.online_page.set_status(f"{label}: ничего не найдено.")
            else:
                self.online_page.set_status(f"{label}: {len(items)} на странице")
            self.online_page.set_more_visible(len(items) >= 10)
            prefetch_images([item.image for item in items if item.image])
            self._log_ui(f"Онлайн-каталог: {label}, страница {page}, модов {len(items)}.")

        def failed(_exc, message, _hint) -> None:
            self.online_page.set_loading(False)
            self.online_page.set_status(f"Не удалось загрузить: {message}", error=True)
            self._log_ui(f"Ошибка онлайн-каталога: {message}")

        self._start_task(
            "Каталог модов",
            work,
            done,
            on_fail=failed,
            busy_message="Загрузка GTA5-Mods.com…",
        )

    def _open_mod(self, listing: ModListing) -> None:
        dialog = ModDialog(listing, self.game, self)
        dialog.install_requested.connect(lambda item: self._later(self._download_online, item))
        dialog.exec()

    def _download_online(self, listing: ModListing) -> None:
        if self._guard_busy():
            return
        if not self._require_game():
            return
        game = self.game
        if game is None:
            return
        game_path = game.path
        self._log_ui(f"Скачивание мода: {listing.title}")

        def work(context):
            client = SiteClient()
            try:
                context.progress(None, "Загрузка страницы мода…")
                details = client.fetch_mod(listing.url)
                context.progress(None, "Получение ссылки на файл…")
                downloaded = client.download_mod(
                    details,
                    progress=_progress_slice(context, 0, 55),
                    cancel=context.cancel_event,
                )
                meta = {
                    "title": details.title or listing.title,
                    "page_url": details.page_url or listing.url,
                    "author": details.author or listing.author,
                    "description": details.description,
                    "image": details.image or listing.image,
                    "deps": details.deps or listing.deps,
                }
                source = prepare_source(
                    downloaded.path,
                    _progress_slice(context, 55, 75),
                    meta=meta,
                )
                try:
                    plan = Analyzer(game_path).analyze(
                        source,
                        _progress_slice(context, 75, 100),
                    )
                except Exception:
                    source.cleanup()
                    raise
                return details, downloaded, source, plan
            finally:
                client.close()

        def done(result) -> None:
            details, downloaded, source, plan = result
            if not plan.installable:
                source.cleanup()
                show_error(
                    self,
                    "Мод не установлен",
                    "В скачанном архиве нет файлов, которые установщик умеет раскладывать автоматически.",
                    hint="Возможно, мод требует ручной установки через OpenIV.",
                )
                self._log_ui(f"Мод без автоматических файлов: {details.title or listing.title}")
                return
            title = details.title or listing.title
            state = SourceState(
                path=downloaded.path,
                title=title,
                meta=source.meta,
                origin="online",
                status="ready",
                message="Скачан",
                source=source,
                plan=plan,
                image=details.image or listing.image,
                page_url=details.page_url or listing.url,
                auto_prompt=True,
            )
            self.sources[state.key] = state
            self.order.append(state.key)
            self.show_page("install")
            self._sync_sources()
            self._log_ui(
                f"Скачан мод «{title}» · файлов: {len(plan.enabled_items)} · "
                f"{downloaded.size_label}."
            )
            self._open_plan([state.key])

        def failed(exc, message, hint) -> None:
            self._log_ui(f"Скачивание не выполнено: {message}")
            if isinstance(exc, cancelled_exceptions()):
                NoticeDialog("Загрузка отменена", "Мод не был добавлен в очередь.", kind="info", parent=self).exec()
                return
            show_error(self, "Не удалось скачать мод", message, hint)

        self._start_task(
            "Скачивание мода",
            work,
            done,
            on_fail=failed,
            busy_message=f"Скачивание: {listing.title}",
            cancelable=True,
        )

    # -------------------------------------------------------------- settings

    def _clear_dependency_cache(self) -> None:
        if self._guard_busy():
            return
        if not confirm(
            self,
            "Очистить кэш зависимостей?",
            "Скачанные архивы Script Hook V, SHVDN и других компонентов будут удалены. "
            "При следующей установке они скачаются заново.",
            accept_text="Очистить",
        ):
            return
        try:
            clear_dependency_cache()
        except OSError as exc:
            show_error(self, "Не удалось очистить кэш", str(exc))
            return
        self._log_ui("Кэш зависимостей очищен.")
        self._set_status("Кэш зависимостей очищен.")

    def _clear_images(self) -> None:
        if self._guard_busy():
            return
        if not confirm(
            self,
            "Очистить изображения?",
            "Кэш обложек модов будет удалён. Они загрузятся снова при открытии каталога.",
            accept_text="Очистить",
        ):
            return
        purge_cached_images()
        self._log_ui("Кэш изображений очищен.")
        self._set_status("Кэш изображений очищен.")

    # ---------------------------------------------------------------- misc

    def _show_about(self) -> None:
        AboutDialog(__version__, self).exec()

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            key = "install"
            page = self.pages[key]
        self._current_page = key
        self.stack.setCurrentWidget(page)
        title, subtitle = PAGE_META.get(key, PAGE_META["install"])
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        for nav_key, button in self.nav_buttons.items():
            button.setChecked(nav_key == key)
        if key == "online":
            QTimer.singleShot(60, self._ensure_online_live)
        elif key == "mods":
            self._refresh_mods()
        elif key == "deps":
            self._refresh_dependencies()
        elif key == "backups":
            self._refresh_backups()
        elif key == "settings":
            self.settings_page.set_game(self.game)

    def show_page_by_key(self, key: str, silent: bool = True) -> None:
        del silent
        self.show_page(key if key in self.pages else "install")

    def _refresh_current(self) -> None:
        key = self._current_page
        if key == "mods":
            self._refresh_mods()
        elif key == "deps":
            self._refresh_dependencies()
        elif key == "backups":
            self._refresh_backups()
        elif key == "online":
            if self._online_mode == "catalog":
                self._online_tried = False
                self._ensure_online_live()
            else:
                self._load_online(
                    self._online_mode,
                    self._online_query,
                    1,
                    append=False,
                )
        elif key == "settings":
            self.settings_page.set_game(self.game)
        else:
            self._sync_sources()

    def _refresh_dependencies(self) -> None:
        states = dependency_states(self.game.path if self.game is not None else None)
        self.deps_page.set_states(states, self.game is not None)

    def _refresh_backups(self) -> None:
        self.backups_page.set_backups(list_backups())

    def _refresh_mods(self) -> None:
        self.mods_page.set_mods(list_installed_mods())

    def _refresh_settings(self) -> None:
        self.settings_page.set_game(self.game)

    # --------------------------------------------------------------- tasks

    def _start_task(
        self,
        name: str,
        fn,
        on_done=None,
        *,
        on_fail=None,
        busy_message: str = "",
        cancelable: bool = False,
    ) -> bool:
        if self._task is not None:
            return False
        task = Task(name, fn, self)
        self._task = task
        self._cancel_enabled = cancelable
        self._set_busy(True, busy_message or f"{name}…")
        task.signals.progress.connect(self._on_task_progress)
        task.signals.log.connect(self._log_ui)
        task.signals.finished.connect(
            lambda result, current=task: self._on_task_done(current, result, on_done)
        )
        task.signals.failed.connect(
            lambda exc, message, hint, current=task: self._on_task_failed(
                current, exc, message, hint, on_fail
            )
        )
        task.finished.connect(task.deleteLater)
        task.start()
        return True

    def _on_task_done(self, task: Task, result, on_done) -> None:
        if task is not self._task:
            return
        self._task = None
        self._cancel_enabled = False
        self._set_busy(False)
        if on_done is not None:
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - keep the window alive
                log.exception("UI callback failed")
                show_error(
                    self,
                    "Ошибка интерфейса",
                    str(exc) or exc.__class__.__name__,
                    "Подробности записаны в журнал.",
                )
        QTimer.singleShot(80, self._pump_queue)

    def _on_task_failed(self, task: Task, exc, message: str, hint: str, on_fail) -> None:
        if task is not self._task:
            return
        self._task = None
        self._cancel_enabled = False
        self._set_busy(False)
        if on_fail is not None:
            try:
                on_fail(exc, message, hint)
            except Exception:  # noqa: BLE001
                log.exception("UI failure callback failed")
        elif isinstance(exc, cancelled_exceptions()):
            NoticeDialog(
                "Операция отменена",
                message or "Операция была отменена.",
                kind="info",
                parent=self,
            ).exec()
        else:
            show_error(self, f"{task.name}: ошибка", message, hint or "")
        QTimer.singleShot(80, self._pump_queue)

    def _on_task_progress(self, percent, message: str) -> None:
        if message:
            self.status_text.setText(message)
            self.status_text.setToolTip(message)
        self.progress.setVisible(True)
        if percent is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, int(percent))))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.cancel_button.setVisible(busy and self._cancel_enabled)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.status_dot.setStyleSheet(f"color: {COLORS['accent']}; font-size: 10px;")
            self.status_text.setText(message or "Работа…")
            self.status_text.setToolTip(message)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.status_dot.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px;")
            self.status_text.setText("Готово")
            self.status_text.setToolTip("")
        self.game_bar.set_enabled_actions(not busy)
        self.install_page.set_busy(busy)
        self.mods_page.set_busy(busy)
        self.deps_page.set_busy(busy)
        self.backups_page.set_busy(busy)
        self.online_page.set_busy(busy)

    def _cancel_task(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self.cancel_button.setEnabled(False)
        self._set_status("Отмена операции…")

    def _guard_busy(self) -> bool:
        if self._task is None:
            return False
        self._set_status("Дождитесь завершения текущей операции.")
        return True

    def _set_status(self, text: str) -> None:
        self.status_text.setText(text)
        self.status_text.setToolTip(text)

    def _later(self, fn, *args) -> None:
        QTimer.singleShot(0, lambda: fn(*args))

    def _log_ui(self, message: str) -> None:
        log.info(message)
        if hasattr(self, "journal_page"):
            self.journal_page.append(message)

    # -------------------------------------------------------------- drag/drop

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self._add_paths(paths)
        else:
            event.ignore()
