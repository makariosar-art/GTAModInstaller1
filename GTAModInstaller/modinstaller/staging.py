from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .archives import GAME_WRAPPERS, ArchiveError, content_root, extract_archive, is_archive
from .config import STAGING_DIR
from .utils import ProgressCB, clean_name_from_file, noop_progress, remove_tree

MAX_NESTED_ARCHIVES = 12
MAX_NESTED_DEPTH = 3


@dataclass
class PreparedSource:
    source_id: str
    display_name: str
    origin: Path
    is_archive: bool
    roots: list[Path] = field(default_factory=list)
    oiv_roots: list[Path] = field(default_factory=list)
    temp_dirs: list[Path] = field(default_factory=list)
    consumed: set[Path] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    folder_label: str = ""

    @property
    def title(self) -> str:
        value = str(self.meta.get("title") or "").strip()
        return value or self.display_name

    @property
    def page_url(self) -> str:
        return str(self.meta.get("page_url") or "")

    @property
    def author(self) -> str:
        return str(self.meta.get("author") or "")

    @property
    def description(self) -> str:
        return str(self.meta.get("description") or "")

    def cleanup(self) -> None:
        for directory in self.temp_dirs:
            remove_tree(directory)
        self.temp_dirs.clear()


class SourceError(Exception):
    pass


def _new_temp_dir(label: str) -> Path:
    path = STAGING_DIR / f"{label}-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _walk_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name.lower() not in {"__macosx", ".git", ".svn", "__pycache__", ".vs"}
        ]
        for name in files:
            result.append(Path(base) / name)
    return result


def _extract_nested(
    source: PreparedSource,
    root: Path,
    progress: ProgressCB,
    depth: int,
    budget: list[int],
) -> None:
    files = _walk_files(root)
    archives = [
        path
        for path in files
        if path not in source.consumed and is_archive(path) and path.suffix.lower() != ".oiv"
    ]
    oivs = [
        path
        for path in files
        if path not in source.consumed and path.suffix.lower() == ".oiv"
    ]

    for index, oiv in enumerate(oivs):
        if budget[0] <= 0:
            source.warnings.append(f"Пропущен вложенный OIV-пакет: {oiv.name}")
            continue
        budget[0] -= 1
        try:
            target_dir = _new_temp_dir("oiv")
            source.temp_dirs.append(target_dir)
            progress(None, f"Распаковка OIV: {oiv.name}")
            extract_archive(oiv, target_dir, progress)
            source.oiv_roots.append(target_dir)
            source.consumed.add(oiv)
        except ArchiveError as exc:
            source.warnings.append(f"OIV-пакет {oiv.name} не распакован: {exc}")
        del index

    if depth >= MAX_NESTED_DEPTH:
        return

    for archive in archives:
        if budget[0] <= 0:
            source.warnings.append(f"Слишком много вложенных архивов — {archive.name} пропущен.")
            continue
        budget[0] -= 1
        try:
            target_dir = _new_temp_dir("nested")
            source.temp_dirs.append(target_dir)
            progress(None, f"Распаковка вложенного архива: {archive.name}")
            extract_archive(archive, target_dir, progress)
            nested_root = content_root(target_dir, aggressive=False)
            source.roots.append(nested_root)
            source.consumed.add(archive)
            _extract_nested(source, nested_root, progress, depth + 1, budget)
        except ArchiveError as exc:
            source.warnings.append(f"Вложенный архив {archive.name} не распакован: {exc}")


def prepare_source(path: Path | str, progress: ProgressCB = noop_progress, meta: dict | None = None) -> PreparedSource:
    origin = Path(path)
    if not origin.exists():
        raise SourceError(f"Путь не найден: {origin}")
    is_archive_file = origin.is_file()
    name_source = origin.name
    source = PreparedSource(
        source_id=uuid.uuid4().hex[:12],
        display_name=clean_name_from_file(name_source),
        origin=origin,
        is_archive=is_archive_file,
        meta=dict(meta or {}),
    )
    progress(0, "Подготовка мода…")
    try:
        if is_archive_file:
            if not is_archive(origin):
                raise SourceError("Это не архив. Перетащите папку мода или ZIP/RAR/7Z/OIV.")
            extracted = _new_temp_dir("mod")
            source.temp_dirs.append(extracted)
            extract_archive(origin, extracted, progress)
            all_files = _walk_files(extracted)
            if not all_files:
                raise SourceError("Архив пуст или не содержит файлов.")
            if origin.suffix.lower() == ".oiv":
                source.oiv_roots.append(extracted)
            else:
                # Keep a single top-level mod folder so scripts/settings.cfg
                # keep the layout the author intended.
                root = content_root(extracted, aggressive=False)
                source.roots.append(root)
                source.folder_label = source.display_name
        else:
            all_entries = [entry for entry in origin.iterdir()]
            if not all_entries:
                raise SourceError("Папка пуста.")
            root = content_root(origin, aggressive=False)
            source.roots.append(root)
            label = origin.name if root == origin else root.name
            if label.strip().lower() not in GAME_WRAPPERS:
                source.folder_label = label

        budget = [MAX_NESTED_ARCHIVES]
        for root in list(source.roots):
            _extract_nested(source, root, progress, 1, budget)

        if not source.roots and not source.oiv_roots:
            raise SourceError("В моде не найдено файлов для установки.")
        progress(100, "Мод подготовлен")
        return source
    except Exception:
        source.cleanup()
        raise
