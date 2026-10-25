from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import KIND_ASI, KIND_CORE, KIND_DLC, KIND_LML, KIND_MODS, KIND_SCRIPT, PlanItem, SourcePlan

from .config import BACKUPS_DIR
from .deps_defs import DEPS
from .game import disk_free, is_game_running
from .utils import (
    ProgressCB,
    copy_file_progress,
    files_word,
    format_local_time,
    human_size,
    is_subpath,
    make_writable,
    noop_progress,
    plural_ru,
    remove_tree,
    safe_slug,
    sanitize_dest_rel,
)

MANIFEST_VERSION = 1
DISK_MARGIN = 48 * 1024 * 1024
DEPENDENCY_TITLES = {dep.name.casefold() for dep in DEPS}
KIND_ORDER = {
    KIND_CORE: 0,
    KIND_ASI: 1,
    KIND_SCRIPT: 2,
    "config": 3,
    KIND_MODS: 4,
    KIND_LML: 5,
    KIND_DLC: 6,
    "root": 7,
    "openiv": 8,
    "manual": 9,
}


class InstallError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class GameRunningError(InstallError):
    pass


class DiskSpaceError(InstallError):
    pass


class InstallCancelled(InstallError):
    pass


@dataclass
class InstalledFile:
    dest_rel: str
    source_title: str
    kind: str
    size: int
    action: str
    replaced: bool
    backup_rel: str = ""


@dataclass
class InstallResult:
    session_id: str
    manifest_path: Path
    title: str
    installed: list[InstalledFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_bytes: int = 0
    replaced: int = 0
    created_dirs: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.installed)

    @property
    def size_label(self) -> str:
        return human_size(self.total_bytes)


@dataclass
class BackupEntry:
    session_id: str
    title: str
    manifest_path: Path
    created_at: float
    game_path: str
    status: str
    file_count: int
    replaced_count: int
    size: int
    finished_at: float = 0.0

    @property
    def date_label(self) -> str:
        return format_local_time(self.created_at)

    @property
    def size_label(self) -> str:
        return human_size(self.size)

    @property
    def status_label(self) -> str:
        return {
            "installed": "Установлено",
            "in_progress": "Прервано",
            "rolled_back": "Откатано",
            "failed": "Ошибка",
        }.get(self.status, self.status or "Неизвестно")


@dataclass
class RollbackResult:
    restored: int = 0
    removed: int = 0
    cleaned: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class InstalledMod:
    session_id: str
    title: str
    manifest_path: Path
    created_at: float
    game_path: str
    records: list[dict] = field(default_factory=list)
    session_title: str = ""

    @property
    def key(self) -> str:
        return f"{self.session_id}:{self.title}"

    @property
    def date_label(self) -> str:
        return format_local_time(self.created_at) if self.created_at else "Дата неизвестна"

    @property
    def file_count(self) -> int:
        return len(self.records)

    @property
    def size(self) -> int:
        return sum(int(record.get("size") or 0) for record in self.records)

    @property
    def size_label(self) -> str:
        return human_size(self.size)

    @property
    def replaced_count(self) -> int:
        return sum(1 for record in self.records if record.get("replaced"))

    @property
    def backed_up_count(self) -> int:
        return sum(
            1
            for record in self.records
            if record.get("replaced") and record.get("backed_up")
        )

    @property
    def unprotected_count(self) -> int:
        return self.replaced_count - self.backed_up_count

    @property
    def dependency(self) -> bool:
        return self.title.casefold() in DEPENDENCY_TITLES


class _Reporter:
    def __init__(self, total: int, file_total: int, progress: ProgressCB) -> None:
        self.total = max(0, total)
        self.file_total = max(0, file_total)
        self.progress = progress
        self.done = 0
        self.files_done = 0
        self._last_percent = -1
        self._last_note = time.monotonic()

    def set_note(self, message: str) -> None:
        self.progress(self._percent(), message)

    def add(self, size: int, message: str, force: bool = False) -> None:
        self.done += max(0, size)
        if self.total > 0:
            percent = self._percent()
        else:
            percent = min(100, int(self.files_done * 100 / max(1, self.file_total)))
        now = time.monotonic()
        if force or percent != self._last_percent or now - self._last_note > 0.12:
            self._last_percent = percent
            self._last_note = now
            self.progress(percent, message)

    def finish_file(self) -> None:
        self.files_done += 1

    def _percent(self) -> int:
        if self.total <= 0:
            return min(100, int(self.files_done * 100 / max(1, self.file_total)))
        return min(100, int(self.done * 100 / self.total))


def _session_id(title: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{safe_slug(title, 'mod', 24)}-{uuid.uuid4().hex[:5]}"


def _target_path(game: Path, dest_rel: str) -> Path:
    clean = sanitize_dest_rel(dest_rel)
    if not clean:
        raise InstallError(f"Недопустимый путь установки: {dest_rel}")
    target = (game / clean).resolve()
    if not is_subpath(target, game):
        raise InstallError(f"Путь выходит за пределы папки игры: {dest_rel}")
    return target


def _ensure_dirs(directory: Path, game: Path, created: set[str]) -> None:
    missing: list[Path] = []
    current = directory
    while current != game and is_subpath(current, game) and not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for path in reversed(missing):
        try:
            path.mkdir()
        except FileExistsError:
            continue
        try:
            created.add(path.relative_to(game).as_posix())
        except ValueError:
            pass


def _flatten_plans(plans: list[SourcePlan]) -> tuple[list[tuple[SourcePlan, PlanItem]], list[str], list[str]]:
    warnings: list[str] = []
    skipped: list[str] = []
    merged: dict[str, tuple[SourcePlan, PlanItem]] = {}
    for plan in plans:
        if not plan.installable:
            warnings.append(f"«{plan.title}»: нет файлов для автоматической установки.")
        manual = plan.manual_items
        if manual:
            skipped.append(
                f"{plan.title}: {len(manual)} {files_word(len(manual))} — ручная установка."
            )
        for item in plan.items:
            if not item.enabled or item.manual:
                continue
            key = item.dest_rel.lower()
            previous = merged.get(key)
            if previous is not None:
                skipped.append(
                    f"{item.src.name}: перекрыт файлом из «{plan.title}» ({item.dest_rel})."
                )
            merged[key] = (plan, item)
    ordered = sorted(
        merged.values(),
        key=lambda pair: (
            KIND_ORDER.get(pair[1].kind, 8),
            pair[1].dest_rel.lower(),
        ),
    )
    return ordered, warnings, skipped


def preflight(
    game_path: Path | str,
    plans: list[SourcePlan],
    *,
    check_running: bool = True,
    make_backups: bool = True,
    progress: ProgressCB = noop_progress,
) -> tuple[list[tuple[SourcePlan, PlanItem]], list[str], list[str]]:
    game = Path(game_path)
    if not game.is_dir():
        raise InstallError("Папка игры не найдена.", "Укажите корректный путь к GTA V в настройках.")
    if not os.access(game, os.W_OK):
        raise InstallError(
            "Нет прав на запись в папку игры.",
            "Запустите установщик от имени администратора или перенесите игру вне Program Files.",
        )
    if check_running:
        running, names = is_game_running()
        if running:
            raise GameRunningError(
                "GTA V запущена. Закройте игру перед установкой.",
                "Обнаружены процессы: " + ", ".join(names),
            )
    pairs, warnings, skipped = _flatten_plans(plans)
    if not pairs:
        raise InstallError(
            "Нечего устанавливать.",
            "Проверьте, что выбраны файлы и папка игры указана верно.",
        )
    need = 0
    backup_need = 0
    for _plan, item in pairs:
        try:
            size = item.src.stat().st_size
        except OSError:
            size = item.size
        need += size
        try:
            target = _target_path(game, item.dest_rel)
        except InstallError:
            continue
        if target.is_file():
            try:
                backup_need += target.stat().st_size
            except OSError:
                backup_need += item.existing_size
    free = disk_free(game)
    required = need + (backup_need if make_backups else 0) + DISK_MARGIN
    if free and required > free:
        raise DiskSpaceError(
            f"Недостаточно места на диске: нужно {human_size(required)}, доступно {human_size(free)}.",
            "Освободите место или отключите резервные копии в настройках.",
        )
    progress(0, "Проверка завершена")
    return pairs, warnings, skipped


def _save_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"Не удалось прочитать резервную копию: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise InstallError("Файл резервной копии повреждён или несовместим.")
    return data


def _active_records(data: dict) -> list[dict]:
    return [
        entry
        for entry in data.get("files") or []
        if isinstance(entry, dict) and entry.get("dest_rel") and not entry.get("removed")
    ]


def _group_title(record: dict, data: dict) -> str:
    return str(record.get("source_title") or data.get("title") or "Установка")


def _manifest_entries() -> list[tuple[Path, dict]]:
    entries: list[tuple[Path, dict]] = []
    try:
        manifests = list(BACKUPS_DIR.glob("*/manifest.json"))
    except OSError:
        return entries
    for manifest in manifests:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            entries.append((manifest, data))
    return entries


def _latest_owners(entries: list[tuple[Path, dict]]) -> dict[str, dict]:
    latest: dict[str, tuple[float, dict]] = {}
    for _path, data in entries:
        if data.get("status") != "installed":
            continue
        fallback = float(data.get("created_at") or 0)
        for record in _active_records(data):
            key = str(record.get("dest_rel") or "").strip().lower()
            if not key:
                continue
            stamp = float(record.get("installed_at") or fallback)
            current = latest.get(key)
            if current is None or stamp >= current[0]:
                latest[key] = (stamp, record)
    return {key: record for key, (_stamp, record) in latest.items()}


def _prune_created_dirs(game: Path, created_dirs: list[str]) -> None:
    for rel in sorted(created_dirs, key=lambda value: value.count("/"), reverse=True):
        try:
            directory = _target_path(game, rel)
        except InstallError:
            continue
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


def install_plans(
    plans: list[SourcePlan],
    game_path: Path | str,
    *,
    make_backups: bool = True,
    check_running: bool = True,
    progress: ProgressCB = noop_progress,
    cancel: threading.Event | None = None,
) -> InstallResult:
    game = Path(game_path)
    pairs, warnings, skipped = preflight(
        game,
        plans,
        check_running=check_running,
        make_backups=make_backups,
        progress=progress,
    )
    titles = [plan.title for plan in plans if plan.installable]
    title = (
        titles[0]
        if len(titles) == 1
        else f"Установка · {len(titles)} {plural_ru(len(titles), 'мод', 'мода', 'модов')}"
    )
    session_id = _session_id(title)
    session_dir = BACKUPS_DIR / session_id
    backup_files_dir = session_dir / "files"
    manifest_path = session_dir / "manifest.json"
    created_dirs: set[str] = set()
    records: list[dict] = []
    installed: list[InstalledFile] = []

    total = 0
    for _plan, item in pairs:
        try:
            total += item.src.stat().st_size
        except OSError:
            total += item.size

    manifest: dict = {
        "version": MANIFEST_VERSION,
        "session_id": session_id,
        "title": title,
        "created_at": time.time(),
        "finished_at": 0,
        "game_path": str(game),
        "status": "in_progress",
        "make_backups": bool(make_backups),
        "files": records,
        "created_dirs": [],
        "skipped": skipped,
        "warnings": warnings,
    }
    session_dir.mkdir(parents=True, exist_ok=True)
    _save_manifest(manifest_path, manifest)

    reporter = _Reporter(total, len(pairs), progress)
    cancelled = False
    failure: Exception | None = None

    try:
        for index, (plan, item) in enumerate(pairs, start=1):
            if cancel is not None and cancel.is_set():
                cancelled = True
                raise InstallCancelled("Установка отменена.")
            try:
                src = Path(item.src)
                if not src.is_file():
                    skipped.append(f"{item.dest_rel}: исходный файл не найден.")
                    reporter.finish_file()
                    continue
                target = _target_path(game, item.dest_rel)
                note = f"[{index}/{len(pairs)}] {item.dest_rel}"
                reporter.set_note(f"Установка: {note}")
                size = src.stat().st_size
                replaced = target.exists()
                backup_rel = ""
                if replaced and target.is_file():
                    make_writable(target)
                    if make_backups:
                        backup_target = backup_files_dir / item.dest_rel
                        reporter.set_note(f"Резервная копия: {item.dest_rel}")
                        copy_file_progress(target, backup_target)
                        backup_rel = item.dest_rel
                        try:
                            backup_size = backup_target.stat().st_size
                        except OSError:
                            backup_size = 0
                        if backup_size:
                            reporter.add(0, f"Резервная копия: {item.dest_rel}")
                for directory in (target.parent,):
                    _ensure_dirs(directory, game, created_dirs)
                record = {
                    "dest_rel": item.dest_rel,
                    "source": item.src.name,
                    "session_id": session_id,
                    "source_title": plan.title,
                    "kind": item.kind,
                    "size": size,
                    "action": item.action,
                    "replaced": replaced,
                    "backed_up": bool(backup_rel),
                    "backup_rel": backup_rel,
                    "installed_at": time.time(),
                }
                records.append(record)
                manifest["created_dirs"] = sorted(created_dirs)
                _save_manifest(manifest_path, manifest)

                def on_bytes(chunk: int, _note: str = note) -> None:
                    if cancel is not None and cancel.is_set():
                        raise InstallCancelled("Установка отменена.")
                    reporter.add(chunk, f"Установка: {_note}")

                copy_file_progress(src, target, on_bytes=on_bytes)
                installed.append(
                    InstalledFile(
                        dest_rel=item.dest_rel,
                        source_title=plan.title,
                        kind=item.kind,
                        size=size,
                        action=item.action,
                        replaced=replaced,
                        backup_rel=backup_rel,
                    )
                )
                reporter.add(0, f"Установка: {note}", force=True)
                reporter.finish_file()
            except InstallCancelled:
                raise
            except OSError as exc:
                raise InstallError(
                    f"Не удалось установить {item.dest_rel}: {exc}",
                    "Проверьте права доступа, свободное место и что игра закрыта.",
                ) from exc
    except InstallCancelled:
        failure = InstallCancelled("Установка отменена.")
    except InstallError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - shown to the user
        failure = InstallError(f"Неожиданная ошибка установки: {exc}")

    if failure is not None:
        _rollback_with_base(game, records, sorted(created_dirs), backup_files_dir, progress, None)
        manifest["status"] = "failed"
        manifest["finished_at"] = time.time()
        manifest["warnings"] = warnings + [str(failure)]
        _save_manifest(manifest_path, manifest)
        raise failure

    manifest["status"] = "installed"
    manifest["finished_at"] = time.time()
    manifest["warnings"] = warnings
    manifest["created_dirs"] = sorted(created_dirs)
    _save_manifest(manifest_path, manifest)
    progress(100, "Установка завершена")
    return InstallResult(
        session_id=session_id,
        manifest_path=manifest_path,
        title=title,
        installed=installed,
        skipped=skipped,
        warnings=warnings,
        total_bytes=sum(entry.size for entry in installed),
        replaced=sum(1 for entry in installed if entry.replaced),
        created_dirs=sorted(created_dirs),
    )


def _rollback_with_base(
    game: Path,
    records: list[dict],
    created_dirs: list[str],
    backup_base: Path,
    progress: ProgressCB,
    cancel: threading.Event | None,
) -> RollbackResult:
    result = RollbackResult()
    total = max(1, len(records))
    for index, record in enumerate(reversed(records), start=1):
        if cancel is not None and cancel.is_set():
            result.warnings.append("Откат прерван пользователем.")
            break
        dest_rel = str(record.get("dest_rel") or "")
        backup_rel = str(record.get("backup_rel") or "")
        replaced = bool(record.get("replaced"))
        try:
            target = _target_path(game, dest_rel)
        except InstallError as exc:
            result.failed += 1
            result.warnings.append(str(exc))
            continue
        progress(int(index * 100 / total), f"Откат: {dest_rel}")
        if replaced:
            if not backup_rel:
                result.failed += 1
                result.warnings.append(f"{dest_rel}: резервная копия не создавалась.")
                continue
            try:
                backup = (backup_base / backup_rel).resolve()
            except OSError:
                backup = backup_base / backup_rel
            if not is_subpath(backup, backup_base) or not backup.is_file():
                result.failed += 1
                result.warnings.append(f"{dest_rel}: резервная копия не найдена.")
                continue
            try:
                make_writable(target)
                copy_file_progress(backup, target)
                result.restored += 1
            except OSError as exc:
                result.failed += 1
                result.warnings.append(f"{dest_rel}: не удалось восстановить ({exc}).")
        else:
            try:
                if target.exists():
                    make_writable(target)
                    target.unlink()
                    result.removed += 1
            except OSError as exc:
                result.failed += 1
                result.warnings.append(f"{dest_rel}: не удалось удалить ({exc}).")
    _prune_created_dirs(game, created_dirs)
    return result


def rollback_manifest(
    manifest_path: Path | str,
    *,
    progress: ProgressCB = noop_progress,
    cancel: threading.Event | None = None,
) -> RollbackResult:
    path = Path(manifest_path)
    data = _load_manifest(path)
    game_value = str(data.get("game_path") or "")
    if not game_value:
        raise InstallError("В резервной копии не указана папка игры.")
    game = Path(game_value)
    if not game.is_dir():
        raise InstallError(
            "Папка игры из резервной копии недоступна.",
            "Подключите диск с игрой или выберите другую копию.",
        )
    if data.get("status") == "rolled_back":
        result = RollbackResult(warnings=["Эта установка уже была откачена."])
        return result
    records = [
        entry
        for entry in data.get("files") or []
        if isinstance(entry, dict) and entry.get("dest_rel") and not entry.get("removed")
    ]
    created_dirs = [str(entry) for entry in data.get("created_dirs") or []]
    backup_base = path.parent / "files"
    result = _rollback_with_base(game, records, created_dirs, backup_base, progress, cancel)
    if result.failed == 0:
        data["status"] = "rolled_back"
    data["rolled_back_at"] = time.time()
    _save_manifest(path, data)
    progress(100, "Откат завершён")
    return result


def _backup_entry(path: Path) -> BackupEntry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    files = _active_records(data)
    return BackupEntry(
        session_id=str(data.get("session_id") or path.parent.name),
        title=str(data.get("title") or "Установка"),
        manifest_path=path,
        created_at=float(data.get("created_at") or path.stat().st_mtime),
        game_path=str(data.get("game_path") or ""),
        status=str(data.get("status") or "unknown"),
        file_count=len(files),
        replaced_count=sum(1 for entry in files if entry.get("replaced")),
        size=sum(int(entry.get("size") or 0) for entry in files),
        finished_at=float(data.get("finished_at") or 0),
    )


def list_backups() -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    try:
        manifests = list(BACKUPS_DIR.glob("*/manifest.json"))
    except OSError:
        return entries
    for manifest in manifests:
        entry = _backup_entry(manifest)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries


def list_installed_mods() -> list[InstalledMod]:
    mods: list[InstalledMod] = []
    for path, data in _manifest_entries():
        if data.get("status") != "installed":
            continue
        groups: dict[str, list[dict]] = {}
        for record in _active_records(data):
            groups.setdefault(_group_title(record, data), []).append(record)
        created_at = float(data.get("created_at") or 0)
        session_title = str(data.get("title") or "")
        for title, records in groups.items():
            mods.append(
                InstalledMod(
                    session_id=str(data.get("session_id") or path.parent.name),
                    title=title,
                    session_title=session_title,
                    manifest_path=path,
                    created_at=created_at,
                    game_path=str(data.get("game_path") or ""),
                    records=records,
                )
            )
    mods.sort(key=lambda mod: (mod.created_at, mod.title.casefold()), reverse=True)
    return mods


def remove_installed_mod(
    manifest_path: Path | str,
    title: str,
    *,
    progress: ProgressCB = noop_progress,
    cancel: threading.Event | None = None,
) -> RollbackResult:
    path = Path(manifest_path)
    try:
        session_dir = path.parent.resolve()
        session_dir.relative_to(BACKUPS_DIR.resolve())
    except (ValueError, OSError):
        raise InstallError("Недопустимый путь установки.")
    try:
        target_manifest = path.resolve()
    except OSError:
        target_manifest = path
    entries = _manifest_entries()
    data: dict | None = None
    for entry_path, entry_data in entries:
        try:
            same = entry_path.resolve() == target_manifest
        except OSError:
            same = entry_path == target_manifest
        if same:
            data = entry_data
            break
    if data is None:
        raise InstallError("Сессия установки не найдена.")
    if data.get("status") == "rolled_back":
        raise InstallError("Эта установка уже откачена.")
    records = [
        record
        for record in _active_records(data)
        if _group_title(record, data) == title
    ]
    if not records:
        raise InstallError("В этой установке больше нет файлов выбранного мода.")
    game_value = str(data.get("game_path") or "")
    if not game_value:
        raise InstallError("В установке не указана папка игры.")
    game = Path(game_value)
    if not game.is_dir():
        raise InstallError(
            "Папка игры недоступна.",
            "Подключите диск с игрой или укажите путь заново.",
        )

    owners = _latest_owners(entries)
    backup_base = path.parent / "files"
    result = RollbackResult()
    changed = False
    total = max(1, len(records))
    for index, record in enumerate(reversed(records), start=1):
        if cancel is not None and cancel.is_set():
            result.warnings.append("Удаление прервано пользователем.")
            break
        dest_rel = str(record.get("dest_rel") or "")
        progress(int(index * 100 / total), f"Удаление: {dest_rel}")
        owner = owners.get(dest_rel.strip().lower())
        if owner is not None and owner is not record:
            result.warnings.append(
                f"{dest_rel}: файл перезаписан более поздней установкой — пропущен."
            )
            continue
        try:
            target = _target_path(game, dest_rel)
        except InstallError as exc:
            result.failed += 1
            result.warnings.append(str(exc))
            continue
        if record.get("replaced"):
            backup_rel = str(record.get("backup_rel") or "")
            if not backup_rel:
                result.failed += 1
                result.warnings.append(
                    f"{dest_rel}: резервная копия не создавалась — файл оставлен."
                )
                continue
            try:
                backup = (backup_base / backup_rel).resolve()
            except OSError:
                backup = backup_base / backup_rel
            if not is_subpath(backup, backup_base) or not backup.is_file():
                result.failed += 1
                result.warnings.append(
                    f"{dest_rel}: резервная копия не найдена — файл оставлен."
                )
                continue
            try:
                make_writable(target)
                copy_file_progress(backup, target)
                result.restored += 1
            except OSError as exc:
                result.failed += 1
                result.warnings.append(f"{dest_rel}: не удалось восстановить ({exc}).")
                continue
        else:
            try:
                if target.exists():
                    make_writable(target)
                    target.unlink()
                    result.removed += 1
                else:
                    result.cleaned += 1
            except OSError as exc:
                result.failed += 1
                result.warnings.append(f"{dest_rel}: не удалось удалить ({exc}).")
                continue
        record["removed"] = True
        record["removed_at"] = time.time()
        changed = True

    if changed:
        _prune_created_dirs(game, [str(entry) for entry in data.get("created_dirs") or []])
        if not _active_records(data):
            data["status"] = "rolled_back"
            data["rolled_back_at"] = time.time()
        _save_manifest(path, data)
    progress(100, "Удаление завершено")
    return result


def delete_backup(manifest_path: Path | str) -> None:
    path = Path(manifest_path)
    base = BACKUPS_DIR.resolve()
    try:
        session_dir = path.parent.resolve()
        session_dir.relative_to(base)
    except (ValueError, OSError):
        raise InstallError("Недопустимый путь резервной копии.")
    if not session_dir.is_dir():
        return
    remove_tree(session_dir, ignore_errors=False)


def backup_size_total() -> int:
    return sum(entry.size for entry in list_backups())
