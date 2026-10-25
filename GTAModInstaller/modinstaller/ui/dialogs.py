from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analyzer import SourcePlan, dependencies_missing
from ..deps_defs import DEP_BY_KEY, dep_name
from ..dependencies import dependency_states
from ..game import GameInfo
from ..installer import InstallResult
from ..site import ModListing
from ..utils import files_word, human_size, open_folder, open_url
from ..workflow import InstallWorkflowResult
from .models import SourceState
from .theme import COLORS, apply_dark_titlebar, make_pill
from .widgets import FlowLayout, StatTile, caption, hline

KIND_LABELS = {
    "asi": "ASI",
    "core": "Ядро",
    "script": "Скрипт",
    "mods": "mods",
    "dlc": "DLC",
    "lml": "LML",
    "openiv": "OpenIV",
    "root": "Корень",
    "config": "Конфиг",
    "manual": "Вручную",
}


def _big_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("DialogTitle")
    label.setWordWrap(True)
    return label


def _copy(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


class BaseDialog(QDialog):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        eyebrow: str = "MOD INSTALLER",
        parent=None,
        width: int = 760,
        height: int = 580,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(width, height)
        self.setMinimumSize(min(width, 560), min(height, 440))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QVBoxLayout()
        header.setContentsMargins(26, 22, 26, 16)
        header.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(10)
        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setObjectName("Eyebrow")
        top.addWidget(eyebrow_label)
        top.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("Ghost")
        close.setFixedSize(30, 30)
        close.setStyleSheet("font-size: 19px; padding: 0;")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.reject)
        top.addWidget(close)
        header.addLayout(top)
        self.title_label = _big_title(title)
        header.addWidget(self.title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("Dim")
            subtitle_label.setWordWrap(True)
            header.addWidget(subtitle_label)
        root.addLayout(header)
        root.addWidget(hline())

        self.content = QVBoxLayout()
        self.content.setContentsMargins(26, 18, 26, 16)
        self.content.setSpacing(14)
        root.addLayout(self.content, 1)

        footer_line = hline()
        root.addWidget(footer_line)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(26, 14, 26, 18)
        self.footer.setSpacing(10)
        self.footer.addStretch(1)
        root.addLayout(self.footer)

    def add_secondary(self, text: str, callback=None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Secondary")
        button.setCursor(Qt.PointingHandCursor)
        if callback is not None:
            button.clicked.connect(callback)
        self.footer.addWidget(button)
        return button

    def add_primary(self, text: str, callback=None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Primary")
        button.setCursor(Qt.PointingHandCursor)
        button.setDefault(True)
        if callback is not None:
            button.clicked.connect(callback)
        self.footer.addWidget(button)
        return button

    def add_danger(self, text: str, callback=None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Danger")
        button.setCursor(Qt.PointingHandCursor)
        if callback is not None:
            button.clicked.connect(callback)
        self.footer.addWidget(button)
        return button

    def add_footer_widget(self, widget: QWidget) -> None:
        self.footer.insertWidget(0, widget)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_dark_titlebar(self)

    def scroll_area(self) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)
        area.setWidget(body)
        return area, layout


def confirm(
    parent,
    title: str,
    message: str,
    *,
    accept_text: str = "Продолжить",
    danger: bool = False,
    hint: str = "",
) -> bool:
    dialog = BaseDialog(title, message, parent=parent, width=520, height=260)
    if hint:
        label = QLabel(hint)
        label.setObjectName("Dim")
        label.setWordWrap(True)
        dialog.content.addWidget(label)
    dialog.content.addStretch(1)
    dialog.add_secondary("Отмена", dialog.reject)
    if danger:
        dialog.add_danger(accept_text, dialog.accept)
    else:
        dialog.add_primary(accept_text, dialog.accept)
    return dialog.exec() == QDialog.Accepted


class NoticeDialog(BaseDialog):
    def __init__(
        self,
        title: str,
        message: str,
        hint: str = "",
        *,
        kind: str = "info",
        url: str = "",
        parent=None,
    ) -> None:
        super().__init__(title, "", parent=parent, width=560, height=280)
        badge = QLabel("✓" if kind == "success" else "!" if kind == "warn" else "×" if kind == "error" else "i")
        badge.setAlignment(Qt.AlignCenter)
        color = (
            COLORS["success"]
            if kind == "success"
            else COLORS["warning"]
            if kind == "warn"
            else COLORS["danger"]
            if kind == "error"
            else COLORS["accent"]
        )
        badge.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 18px;"
            "font-size: 17px; font-weight: 700;"
        )
        badge.setFixedSize(36, 36)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(badge, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        message_label = QLabel(message)
        message_label.setWordWrap(True)
        text.addWidget(message_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Dim")
            hint_label.setWordWrap(True)
            hint_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            text.addWidget(hint_label)
        row.addLayout(text, 1)
        self.content.addLayout(row)
        self.content.addStretch(1)
        if url:
            dialog_self = self

            def open_remote() -> None:
                open_url(url)
                dialog_self.accept()

            dialog_self.add_secondary("Открыть сайт", open_remote)
        self.add_primary("Понятно", self.accept)


def show_error(parent, title: str, message: str, hint: str = "", url: str = "") -> None:
    NoticeDialog(title, message, hint, kind="error", url=url, parent=parent).exec()


class PlanDialog(BaseDialog):
    def __init__(
        self,
        plans: list[SourcePlan],
        game: GameInfo | None,
        *,
        make_backups: bool,
        auto_install_deps: bool = True,
        parent=None,
    ) -> None:
        total_items = sum(len(plan.items) for plan in plans)
        super().__init__(
            "План установки" if len(plans) == 1 else f"План установки · {len(plans)} мода",
            "Проверьте, какие файлы будут записаны в папку игры.",
            parent=parent,
            width=1020,
            height=780,
        )
        self.plans = plans
        self.game = game
        enabled = [item for plan in plans for item in plan.enabled_items]
        manual = [item for plan in plans for item in plan.manual_items]
        total_size = sum(item.size for item in enabled)
        replaced = sum(1 for item in enabled if item.conflict)
        ignored = sum(len(plan.ignored) for plan in plans)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        stats.addWidget(StatTile(str(len(enabled)), "файлов", accent=True))
        stats.addWidget(StatTile(str(replaced), "замен"))
        stats.addWidget(StatTile(human_size(total_size), "объём"))
        stats.addWidget(StatTile(str(len(manual)), "вручную"))
        self.content.addLayout(stats)

        missing = dependencies_missing(plans)
        states = dependency_states(game.path if game else None)
        auto_missing: dict[str, list[str]] = {}
        manual_missing: dict[str, list[str]] = {}
        for key, reasons in missing.items():
            dep = DEP_BY_KEY.get(key)
            if dep is None:
                continue
            state = states.get(key)
            if state is not None and state.installed is True:
                continue
            if dep.auto:
                auto_missing[key] = reasons
            else:
                manual_missing[key] = reasons

        scroll, body = self.scroll_area()
        self.content.addWidget(scroll, 1)

        if not make_backups:
            warn = QLabel(
                "Резервные копии отключены: заменённые файлы не получится восстановить автоматически."
            )
            warn.setObjectName("WarningText")
            warn.setWordWrap(True)
            body.addWidget(warn)

        self.deps_checkbox = None
        if auto_missing:
            deps_card = QFrame()
            deps_card.setObjectName("PanelAlt")
            deps_layout = QVBoxLayout(deps_card)
            deps_layout.setContentsMargins(14, 12, 14, 12)
            deps_layout.setSpacing(7)
            from PySide6.QtWidgets import QCheckBox

            row = QHBoxLayout()
            row.setSpacing(10)
            self.deps_checkbox = QCheckBox("Установить отсутствующие зависимости")
            self.deps_checkbox.setChecked(auto_install_deps)
            row.addWidget(self.deps_checkbox)
            row.addStretch(1)
            count = QLabel(f"{len(auto_missing)}")
            count.setObjectName("Accent")
            row.addWidget(count)
            deps_layout.addLayout(row)
            for key, reasons in auto_missing.items():
                line = QHBoxLayout()
                line.setSpacing(8)
                dot = QLabel("◆")
                dot.setObjectName("Bullet")
                line.addWidget(dot)
                name = QLabel(dep_name(key))
                name.setStyleSheet("font-weight: 600;")
                line.addWidget(name)
                reason = QLabel(" · ".join(dict.fromkeys(reasons))[:160])
                reason.setObjectName("Muted")
                reason.setStyleSheet("font-size: 11px;")
                line.addWidget(reason, 1)
                deps_layout.addLayout(line)
            hint = QLabel(
                "Зависимости будут скачаны с официальных источников, установлены вместе с модом "
                "и также защищены резервной копией."
            )
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 11px;")
            deps_layout.addWidget(hint)
            body.addWidget(deps_card)

        if manual_missing:
            manual_card = QFrame()
            manual_card.setObjectName("NoticeWarn")
            manual_layout = QVBoxLayout(manual_card)
            manual_layout.setContentsMargins(14, 11, 14, 11)
            manual_layout.setSpacing(6)
            title = QLabel("Потребуется ручная установка")
            title.setStyleSheet(f"color: {COLORS['warning']}; font-weight: 700;")
            manual_layout.addWidget(title)
            for key in manual_missing:
                dep = DEP_BY_KEY.get(key)
                if dep is None:
                    continue
                row = QHBoxLayout()
                name = QLabel(dep.name)
                name.setStyleSheet("font-weight: 600;")
                row.addWidget(name)
                subtitle = QLabel(dep.subtitle)
                subtitle.setObjectName("Muted")
                subtitle.setStyleSheet("font-size: 11px;")
                row.addWidget(subtitle, 1)
                link = QPushButton("Открыть сайт")
                link.setObjectName("Link")
                link.setCursor(Qt.PointingHandCursor)
                link.clicked.connect(lambda _=False, url=dep.url: open_url(url))
                row.addWidget(link)
                manual_layout.addLayout(row)
            body.addWidget(manual_card)

        warnings = []
        for plan in plans:
            for warning in plan.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        if warnings:
            warn_card = QFrame()
            warn_card.setObjectName("SoftPanel")
            warn_layout = QVBoxLayout(warn_card)
            warn_layout.setContentsMargins(14, 11, 14, 11)
            warn_layout.setSpacing(5)
            title = QLabel("Обратите внимание")
            title.setObjectName("SectionCaption")
            warn_layout.addWidget(title)
            for warning in warnings[:8]:
                label = QLabel("•  " + warning)
                label.setWordWrap(True)
                label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
                warn_layout.addWidget(label)
            if len(warnings) > 8:
                more = QLabel(f"И ещё {len(warnings) - 8} предупреждений — смотрите журнал.")
                more.setObjectName("Muted")
                more.setStyleSheet("font-size: 11px;")
                warn_layout.addWidget(more)
            body.addWidget(warn_card)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["МОД", "КУДА", "ФАЙЛ", "ТИП", "РАЗМЕР", "СТАТУС"])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(230)
        table.setMaximumHeight(330)
        headers = table.horizontalHeader()
        headers.setStretchLastSection(False)
        for column in range(5):
            headers.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        headers.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        rows = []
        for plan in plans:
            for item in plan.items:
                if item.manual:
                    rows.append((plan, item, "Вручную"))
                elif item.enabled:
                    rows.append((plan, item, "Замена" if item.conflict else "Новый"))
        table.setRowCount(len(rows))
        for row_index, (plan, item, status) in enumerate(rows):
            values = [
                plan.title,
                item.dest_rel or "—",
                item.src.name,
                KIND_LABELS.get(item.kind, item.kind),
                human_size(item.size),
                status,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(plan.title)
                if item.manual:
                    cell.setForeground(QColor(COLORS["muted"]))
                elif status == "Замена":
                    cell.setForeground(QColor(COLORS["warning"]))
                table.setItem(row_index, column, cell)
        table.resizeColumnsToContents()
        body.addWidget(table)
        if ignored:
            note = QLabel(f"Служебные файлы и документация пропущены: {ignored}.")
            note.setObjectName("Muted")
            note.setStyleSheet("font-size: 11px;")
            body.addWidget(note)
        if manual:
            note = QLabel(
                f"Файлы для ручной установки: {len(manual)} — автоматически не записываются."
            )
            note.setObjectName("WarningText")
            note.setWordWrap(True)
            body.addWidget(note)
        body.addStretch(1)

        header_note = QLabel(
            "Игра должна быть закрыта."
            + ("  Резервные копии заменяемых файлов будут сохранены." if make_backups else "")
        )
        header_note.setObjectName("Muted")
        header_note.setStyleSheet("font-size: 11px;")
        self.add_footer_widget(header_note)
        self.add_secondary("Отмена", self.reject)
        self.install_button = self.add_primary(
            f"Установить {len(enabled)} {files_word(len(enabled))}" if enabled else "Установить",
            self.accept,
        )
        self.install_button.setEnabled(bool(enabled))

    @property
    def install_dependencies(self) -> bool:
        return bool(self.deps_checkbox and self.deps_checkbox.isChecked())


class InstallResultDialog(BaseDialog):
    rollback_requested = Signal()
    backups_requested = Signal()

    def __init__(
        self,
        result: InstallWorkflowResult,
        plans: list[SourcePlan],
        game: GameInfo | None,
        *,
        dependency_only: bool = False,
        parent=None,
    ) -> None:
        install = result.install
        super().__init__(
            "Зависимости установлены" if dependency_only else "Мод установлен",
            install.title,
            parent=parent,
            width=820,
            height=680,
        )
        self._game = game
        hero = QFrame()
        hero.setObjectName("HeroBanner")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(18)
        check = QLabel("✓")
        check.setAlignment(Qt.AlignCenter)
        check.setFixedSize(58, 58)
        check.setStyleSheet(
            f"color: {COLORS['accent']}; background: rgba(198,255,61,0.10);"
            f"border: 1px solid {COLORS['accent_border']}; border-radius: 29px;"
            "font-size: 26px; font-weight: 700;"
        )
        hero_layout.addWidget(check)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = QLabel(
            "Все файлы на месте."
            if dependency_only
            else "Установка завершена без ошибок."
        )
        title.setObjectName("HeroTitle")
        text.addWidget(title)
        subtitle = QLabel(
            "Можно запускать игру. Резервная копия сохранена — при необходимости откатите изменения."
        )
        subtitle.setObjectName("Dim")
        subtitle.setWordWrap(True)
        text.addWidget(subtitle)
        hero_layout.addLayout(text, 1)
        self.content.addWidget(hero)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        stats.addWidget(StatTile(str(install.file_count), "файлов", accent=True))
        stats.addWidget(StatTile(str(install.replaced), "заменено"))
        stats.addWidget(StatTile(human_size(install.total_bytes), "записано"))
        deps_count = len(result.dependencies.installed)
        stats.addWidget(StatTile(str(deps_count), "зависимостей"))
        self.content.addLayout(stats)

        scroll, body = self.scroll_area()
        self.content.addWidget(scroll, 1)

        if result.dependencies.installed:
            dep_card = QFrame()
            dep_card.setObjectName("PanelAlt")
            dep_layout = QVBoxLayout(dep_card)
            dep_layout.setContentsMargins(14, 11, 14, 11)
            dep_layout.setSpacing(5)
            dep_layout.addWidget(caption("Установленные зависимости"))
            for name in result.dependencies.installed:
                label = QLabel("✓  " + name)
                label.setStyleSheet(f"color: {COLORS['success']}; font-size: 12px;")
                dep_layout.addWidget(label)
            body.addWidget(dep_card)

        if result.dependencies.failed:
            fail_card = QFrame()
            fail_card.setObjectName("NoticeWarn")
            fail_layout = QVBoxLayout(fail_card)
            fail_layout.setContentsMargins(14, 11, 14, 11)
            fail_layout.setSpacing(5)
            title = QLabel("Зависимости с ошибками")
            title.setStyleSheet(f"color: {COLORS['warning']}; font-weight: 700;")
            fail_layout.addWidget(title)
            for name, reason in result.dependencies.failed:
                label = QLabel(f"•  {name}: {reason}")
                label.setWordWrap(True)
                label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
                fail_layout.addWidget(label)
            hint = QLabel(
                "Закройте окно — установщик предложит установить их повторно. "
                "Также можно повторить установку на странице «Зависимости»."
            )
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 11px;")
            fail_layout.addWidget(hint)
            body.addWidget(fail_card)

        manual_items = [item for plan in plans for item in plan.manual_items]
        dlc_names: list[str] = []
        for plan in plans:
            for name in plan.dlc_names:
                if name not in dlc_names:
                    dlc_names.append(name)

        if manual_items:
            manual_card = QFrame()
            manual_card.setObjectName("NoticeWarn")
            manual_layout = QVBoxLayout(manual_card)
            manual_layout.setContentsMargins(14, 11, 14, 11)
            manual_layout.setSpacing(4)
            title = QLabel("Осталось установить вручную")
            title.setStyleSheet(f"color: {COLORS['warning']}; font-weight: 700;")
            manual_layout.addWidget(title)
            for item in manual_items[:10]:
                label = QLabel(f"•  {item.src.name}: {item.note}")
                label.setWordWrap(True)
                label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
                manual_layout.addWidget(label)
            if len(manual_items) > 10:
                more = QLabel(
                    f"И ещё {len(manual_items) - 10} "
                    f"{files_word(len(manual_items) - 10)}."
                )
                more.setObjectName("Muted")
                manual_layout.addWidget(more)
            body.addWidget(manual_card)

        if dlc_names:
            dlc_card = QFrame()
            dlc_card.setObjectName("Notice")
            dlc_layout = QVBoxLayout(dlc_card)
            dlc_layout.setContentsMargins(14, 11, 14, 11)
            dlc_layout.setSpacing(6)
            title = QLabel("Активируйте add-on DLC")
            title.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 700;")
            dlc_layout.addWidget(title)
            intro = QLabel(
                "Файлы DLC уже в папке игры, но игра увидит их только после добавления строк "
                "в dlclist.xml через OpenIV."
            )
            intro.setObjectName("Dim")
            intro.setWordWrap(True)
            intro.setStyleSheet("font-size: 11px;")
            dlc_layout.addWidget(intro)
            lines = "\n".join(f"<Item>dlcpacks:/{name}/</Item>" for name in dlc_names)
            code = QLabel(lines)
            code.setObjectName("Mono")
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            code.setStyleSheet(
                f"background: rgba(0,0,0,0.25); border: 1px solid {COLORS['border']};"
                "border-radius: 7px; padding: 8px 10px; color: #D9E6D2;"
            )
            dlc_layout.addWidget(code)
            copy_button = QPushButton("Копировать строки")
            copy_button.setObjectName("Link")
            copy_button.setCursor(Qt.PointingHandCursor)
            copy_button.clicked.connect(lambda: _copy(lines))
            dlc_layout.addWidget(copy_button, 0, Qt.AlignLeft)
            body.addWidget(dlc_card)

        path_note = QLabel(f"Папка игры:  {game.path if game else install.manifest_path}")
        path_note.setObjectName("Muted")
        path_note.setStyleSheet("font-size: 11px;")
        body.addWidget(path_note)
        body.addStretch(1)

        if game is not None:
            open_game = self.add_secondary("Открыть папку игры", lambda: open_folder(game.path))
            open_game.setToolTip(str(game.path))
        self.add_secondary("Резервные копии", self._open_backups)
        self.add_danger("Откатить", self._rollback)
        self.add_primary("Готово", self.accept)

    def _open_backups(self) -> None:
        self.backups_requested.emit()
        self.accept()

    def _rollback(self) -> None:
        self.rollback_requested.emit()
        self.accept()


class ModDialog(BaseDialog):
    install_requested = Signal(object)

    def __init__(self, listing: ModListing, game: GameInfo | None, parent=None) -> None:
        super().__init__(
            "Онлайн-мод",
            "Скачивание с GTA5-Mods.com и автоматическая установка.",
            eyebrow="GTA5-MODS.COM",
            parent=parent,
            width=920,
            height=610,
        )
        self.listing = listing
        content = QHBoxLayout()
        content.setSpacing(22)
        self.content.addLayout(content, 1)

        left = QVBoxLayout()
        left.setSpacing(10)
        from .image_loader import AsyncCover

        cover = AsyncCover(height=190)
        cover.set_placeholder(listing.tag or "GTA5-MODS")
        cover.set_url(listing.image)
        cover.setStyleSheet("border-radius: 12px;")
        left.addWidget(cover)
        title = QLabel(listing.title)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 18px;")
        left.addWidget(title)
        author = QLabel(listing.author or "Автор неизвестен")
        author.setObjectName("Dim")
        left.addWidget(author)
        tags = QHBoxLayout()
        tags.setSpacing(6)
        if listing.tag:
            tags.addWidget(make_pill(listing.tag.upper(), "muted"))
        if listing.version:
            tags.addWidget(make_pill("v" + listing.version, "muted"))
        if listing.deps:
            tags.addWidget(make_pill("+ ЗАВИСИМОСТИ", "accent"))
        tags.addStretch(1)
        left.addLayout(tags)
        left.addStretch(1)
        install = QPushButton("Скачать и установить")
        install.setObjectName("Primary")
        install.setCursor(Qt.PointingHandCursor)
        install.setMinimumHeight(40)
        install.clicked.connect(self._request_install)
        left.addWidget(install)
        site = QPushButton("Открыть на сайте")
        site.setObjectName("Secondary")
        site.setCursor(Qt.PointingHandCursor)
        site.clicked.connect(lambda: open_url(listing.url))
        left.addWidget(site)
        close = QPushButton("Закрыть")
        close.setObjectName("Ghost")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.reject)
        left.addWidget(close)
        content.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(12)
        desc_title = QLabel("ОПИСАНИЕ")
        desc_title.setObjectName("SectionCaption")
        body_layout.addWidget(desc_title)
        description = QLabel(listing.summary or "Описание загрузится со страницы мода во время скачивания.")
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        description.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        body_layout.addWidget(description)
        body_layout.addWidget(hline())
        deps_title = QLabel("ЗАВИСИМОСТИ")
        deps_title.setObjectName("SectionCaption")
        body_layout.addWidget(deps_title)
        if listing.deps:
            for key in listing.deps:
                dep = DEP_BY_KEY.get(key)
                if dep is None:
                    continue
                row = QHBoxLayout()
                dot = QLabel("◆")
                dot.setObjectName("Bullet")
                row.addWidget(dot)
                name = QLabel(dep.name)
                name.setStyleSheet("font-weight: 600;")
                row.addWidget(name)
                subtitle = QLabel(dep.subtitle)
                subtitle.setObjectName("Muted")
                subtitle.setStyleSheet("font-size: 11px;")
                row.addWidget(subtitle, 1)
                body_layout.addLayout(row)
        else:
            note = QLabel("Установщик определит зависимости по файлам и описанию мода.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            body_layout.addWidget(note)
        body_layout.addWidget(hline())
        steps_title = QLabel("ЧТО БУДЕТ ДАЛЬШЕ")
        steps_title.setObjectName("SectionCaption")
        body_layout.addWidget(steps_title)
        steps = [
            "Установщик скачает основной файл мода.",
            "Архив будет распакован и разобран по папкам игры.",
            "Откроется план установки — вы подтверждаете запись файлов.",
            "Недостающие зависимости установятся вместе с модом.",
        ]
        for index, step in enumerate(steps, start=1):
            row = QHBoxLayout()
            number = QLabel(f"{index:02d}")
            number.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 700;")
            number.setFixedWidth(26)
            row.addWidget(number, 0, Qt.AlignTop)
            label = QLabel(step)
            label.setObjectName("Dim")
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px;")
            row.addWidget(label, 1)
            body_layout.addLayout(row)
        if game is None:
            warn = QLabel("Игра не выбрана — перед скачиванием нужно указать папку GTA V.")
            warn.setObjectName("WarningText")
            warn.setWordWrap(True)
            body_layout.addWidget(warn)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        right.addWidget(scroll, 1)
        content.addLayout(right, 1)

    def _request_install(self) -> None:
        self.install_requested.emit(self.listing)
        self.accept()


class GamePickerDialog(BaseDialog):
    def __init__(self, games: list[GameInfo], parent=None) -> None:
        super().__init__(
            "Найдена GTA V" if games else "Игра не найдена",
            "Выберите установку, которую будет использовать установщик.",
            eyebrow="ПОИСК ИГРЫ",
            parent=parent,
            width=720,
            height=460,
        )
        self.games = games
        self.selected: GameInfo | None = games[0] if games else None
        if not games:
            label = QLabel(
                "Автоматический поиск не нашёл GTA5.exe. Укажите папку игры вручную — "
                "выберите папку, где лежит GTA5.exe (или GTA5_Enhanced.exe)."
            )
            label.setWordWrap(True)
            label.setObjectName("Dim")
            self.content.addWidget(label)
        else:
            self.list = QListWidget()
            for game in games:
                item = QListWidgetItem(
                    f"{game.label}   ·   {game.version or 'версия неизвестна'}\n{game.path}"
                )
                item.setData(Qt.UserRole, game)
                self.list.addItem(item)
            self.list.setCurrentRow(0)
            self.list.setMinimumHeight(200)
            self.content.addWidget(self.list, 1)
            note = QLabel("Рекомендуется выбрать GTA V Legacy, если мод не поддерживает Enhanced.")
            note.setObjectName("Muted")
            note.setStyleSheet("font-size: 11px;")
            self.content.addWidget(note)
        self._manual = False
        self.add_secondary("Указать вручную", self._pick_manual)
        self.add_secondary("Отмена", self.reject)
        if games:
            self.add_primary("Использовать", self._accept_selected)

    def _pick_manual(self) -> None:
        self._manual = True
        self.accept()

    def _accept_selected(self) -> None:
        if hasattr(self, "list"):
            item = self.list.currentItem()
            if item is not None:
                self.selected = item.data(Qt.UserRole)
        self.accept()

    @property
    def manual_requested(self) -> bool:
        return self._manual


class ReadmeDialog(BaseDialog):
    def __init__(self, title: str, readmes, parent=None) -> None:
        super().__init__(title, "Инструкция автора мода", eyebrow="README", parent=parent, width=820, height=620)
        self.readmes = list(readmes)
        content = QHBoxLayout()
        content.setSpacing(14)
        self.content.addLayout(content, 1)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        font = self.view.font()
        font.setFamily("Cascadia Mono")
        font.setPointSize(9)
        self.view.setFont(font)
        if len(self.readmes) > 1:
            self.names = QListWidget()
            self.names.setFixedWidth(210)
            for doc in self.readmes:
                self.names.addItem(doc.name)
            self.names.currentRowChanged.connect(self._show_doc)
            content.addWidget(self.names)
        content.addWidget(self.view, 1)
        if self.readmes:
            self.view.setPlainText(self.readmes[0].text)
        copy_button = self.add_secondary("Копировать", self._copy_text)
        copy_button.setToolTip("Скопировать текст инструкции")
        self.add_primary("Закрыть", self.accept)

    def _show_doc(self, index: int) -> None:
        if 0 <= index < len(self.readmes):
            self.view.setPlainText(self.readmes[index].text)

    def _copy_text(self) -> None:
        _copy(self.view.toPlainText())


class AboutDialog(BaseDialog):
    def __init__(self, version: str, parent=None) -> None:
        super().__init__("GTA V Mod Installer", "Установщик модов для Grand Theft Auto V", parent=parent, width=620, height=560)
        mark = QLabel("V")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(52, 52)
        mark.setStyleSheet(
            f"color: #0A0D0A; background: {COLORS['accent']}; border-radius: 14px;"
            "font-family: Bahnschrift; font-size: 26px; font-weight: 800;"
        )
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(mark)
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel("MOD INSTALLER")
        name.setObjectName("BrandTitle")
        info.addWidget(name)
        ver = QLabel(f"Версия {version}")
        ver.setObjectName("Muted")
        info.addWidget(ver)
        row.addLayout(info)
        row.addStretch(1)
        self.content.addLayout(row)
        self.content.addWidget(hline())
        text = QLabel(
            "Приложение распаковывает архивы модов, раскладывает файлы по папкам GTA V, "
            "находит и устанавливает зависимости, сохраняет резервные копии и умеет "
            "откатывать установку.\n\n"
            "Моды можно перетаскивать в окно или скачивать со страницы GTA5-Mods.com. "
            "Перед записью в игру всегда показывается план установки."
        )
        text.setWordWrap(True)
        text.setObjectName("Dim")
        self.content.addWidget(text)
        links = QLabel(
            f"<a style='color:{COLORS['accent']}' href='https://www.gta5-mods.com'>GTA5-Mods.com</a>"
            " &nbsp;·&nbsp; "
            f"<a style='color:{COLORS['accent']}' href='https://www.dev-c.com/gtav/scripthookv/'>Script Hook V</a>"
            " &nbsp;·&nbsp; "
            f"<a style='color:{COLORS['accent']}' href='https://openiv.com/'>OpenIV</a>"
        )
        links.setOpenExternalLinks(True)
        self.content.addWidget(links)
        self.content.addWidget(hline())
        disclaimer = QLabel(
            "Неофициальное приложение. Не связано с Rockstar Games, Take-Two Interactive, "
            "GTA5-Mods.com или авторами модов. Устанавливайте только те моды, которым доверяете."
        )
        disclaimer.setObjectName("Muted")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("font-size: 11px;")
        self.content.addWidget(disclaimer)
        self.content.addStretch(1)
        self.add_primary("Закрыть", self.accept)
