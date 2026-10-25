from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import DOWNLOADS_DIR, STAGING_DIR
from .deps_defs import DEP_BY_KEY, DEPS
from .staging import PreparedSource, prepare_source
from .site import (
    GITHUB_DEPS,
    DownloadCancelled,
    ManualDependencyError,
    SiteClient,
    SiteError,
)
from .utils import ProgressCB, noop_progress, remove_tree

CACHE_TTL = 21 * 24 * 3600


@dataclass
class DepState:
    key: str
    name: str
    installed: bool | None
    detail: str = ""
    version: str = ""
    auto: bool = False

    @property
    def status_label(self) -> str:
        if self.installed is True:
            return "Установлено"
        if self.installed is False:
            return "Не установлено"
        return "Вручную"


def _files(directory: Path) -> list[Path]:
    try:
        return [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return []


def _has_file(directory: Path, names: tuple[str, ...]) -> bool:
    lowered = {name.lower() for name in names}
    return any(entry.name.lower() in lowered for entry in _files(directory))


def _exact_file(directory: Path, name: str) -> Path | None:
    target = name.lower()
    for entry in _files(directory):
        if entry.name.lower() == target:
            return entry
    return None


def _find_named(directory: Path, needle: str, *, suffix: str = "") -> Path | None:
    needle = needle.lower()
    for entry in _files(directory):
        name = entry.name.lower()
        if needle in name and (not suffix or name.endswith(suffix.lower())):
            return entry
    return None


def dependency_states(game_path: Path | str | None) -> dict[str, DepState]:
    game = Path(game_path) if game_path else None
    states: dict[str, DepState] = {}
    scripts = game / "scripts" if game else None

    for dep in DEPS:
        installed: bool | None = False
        detail = "Папка игры не выбрана." if game is None else "Не найден."
        version = ""
        if game is not None and game.is_dir():
            if dep.key == "shv":
                library = _exact_file(game, "ScriptHookV.dll")
                has_dll = library is not None
                has_loader = _has_file(game, ("dinput8.dll", "xinput1_4.dll"))
                if has_dll and has_loader:
                    installed = True
                    detail = "ScriptHookV.dll и ASI-загрузчик на месте."
                    version = _dll_version_label(library)
                elif has_dll:
                    installed = False
                    detail = "ScriptHookV.dll есть, но нет ASI-загрузчика (dinput8.dll)."
                else:
                    detail = "Не найден ScriptHookV.dll."
            elif dep.key == "shvdn":
                path = _find_named(game, "scripthookvdotnet", suffix=".dll")
                if path is not None:
                    installed = True
                    detail = path.name
                    version = _dll_version_label(path)
                else:
                    detail = "Не найден ScriptHookVDotNet*.dll."
            elif dep.key == "lemonui":
                path = None
                for directory in (scripts, game):
                    if directory and directory.is_dir():
                        path = _find_named(directory, "lemonui", suffix=".dll")
                        if path is not None:
                            break
                if path is not None:
                    installed = True
                    detail = path.name
                else:
                    detail = "Не найден LemonUI.SHVDN3.dll в папке scripts."
            elif dep.key == "nativeui":
                path = _find_named(scripts, "nativeui", suffix=".dll") if scripts else None
                if path is not None:
                    installed = True
                    detail = path.name
                else:
                    detail = "Не найден NativeUI.dll в папке scripts."
            elif dep.key == "lua":
                path = _find_named(game, "lua", suffix=".asi")
                if path is not None:
                    installed = True
                    detail = path.name
                else:
                    detail = "Не найден LUA.asi."
            elif dep.key == "heap":
                path = _find_named(game, "heapadjuster", suffix=".asi")
                if path is not None:
                    installed = True
                    detail = path.name
                else:
                    detail = "Не найден HeapAdjuster.asi."
            elif dep.key == "packfile":
                path = _find_named(game, "packfilelimitadjuster", suffix=".asi")
                if path is not None:
                    installed = True
                    detail = path.name
                else:
                    detail = "Не найден PackfileLimitAdjuster.asi."
            elif dep.key == "lml":
                has_dir = (game / "lml").is_dir()
                has_asi = _find_named(game, "lml", suffix=".asi") is not None
                if has_dir and has_asi:
                    installed = True
                    detail = "Lenny's Mod Loader установлен."
                elif has_asi:
                    installed = False
                    detail = "Есть LML.asi, но нет папки lml."
                else:
                    detail = "LML не установлен."
            elif dep.key == "openiv":
                has_asi = _find_named(game, "openiv", suffix=".asi") is not None
                if has_asi:
                    installed = True
                    detail = "OpenIV.asi установлен."
                else:
                    detail = "OpenIV не подключён к игре."
            elif dep.key == "gameconfig":
                installed = None
                detail = "Устанавливается вручную через OpenIV."
        else:
            installed = None
            detail = "Папка игры не выбрана."
        if not dep.auto and dep.key not in {"lml", "openiv", "gameconfig"}:
            installed = None
        states[dep.key] = DepState(
            key=dep.key,
            name=dep.name,
            installed=installed,
            detail=detail,
            version=version,
            auto=dep.auto,
        )
    # Manual dependencies are reported as unknown even when detected, because the
    # installer never manages them automatically.
    for key in ("lml", "openiv", "gameconfig"):
        state = states.get(key)
        if state and state.installed is True:
            state.installed = None
            state.detail += " (обнаружено, управляется вручную)"
    return states


def _dll_version_label(path: Path | None) -> str:
    if path is None:
        return ""
    from .game import file_version

    return file_version(path)


def missing_dependencies(required: set[str], game_path: Path | str | None) -> dict[str, str]:
    states = dependency_states(game_path)
    missing: dict[str, str] = {}
    for key in required:
        state = states.get(key)
        if state is None:
            continue
        if state.installed is False:
            missing[key] = state.detail
    return missing


def _cache_dir(key: str) -> Path:
    directory = DOWNLOADS_DIR / "deps" / key
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _archive_candidates(directory: Path) -> list[Path]:
    try:
        entries = [
            entry
            for entry in directory.iterdir()
            if entry.is_file()
            and entry.suffix.lower() in {".zip", ".rar", ".7z", ".oiv"}
            and not entry.name.endswith(".part")
        ]
    except OSError:
        return []
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    return entries


def _read_cache_meta(directory: Path) -> dict:
    try:
        data = json.loads((directory / "cache.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache_meta(directory: Path, data: dict) -> None:
    try:
        (directory / "cache.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _cached(key: str) -> tuple[Path, dict] | None:
    directory = _cache_dir(key)
    archives = _archive_candidates(directory)
    if not archives:
        return None
    meta = _read_cache_meta(directory)
    return archives[0], meta


def _is_fresh(archive: Path, meta: dict) -> bool:
    stamp = float(meta.get("downloaded_at") or 0)
    if not stamp:
        try:
            stamp = archive.stat().st_mtime
        except OSError:
            return False
    return time.time() - stamp < CACHE_TTL


def _cleanup_cache_duplicates(key: str, keep: Path) -> None:
    directory = _cache_dir(key)
    for archive in _archive_candidates(directory):
        if archive.resolve() == keep.resolve():
            continue
        try:
            archive.unlink()
        except OSError:
            pass


@dataclass
class DepAcquisition:
    key: str
    archive: Path
    version: str
    from_cache: bool
    warning: str = ""


def acquire_dependency(
    key: str,
    *,
    progress: ProgressCB = noop_progress,
    cancel=None,
    force: bool = False,
) -> DepAcquisition:
    dep = DEP_BY_KEY.get(key)
    if dep is None:
        raise SiteError(f"Неизвестная зависимость: {key}")
    if not dep.auto:
        raise ManualDependencyError(f"«{dep.name}» устанавливается вручную.", dep.url)
    cached = _cached(key)
    if cached and not force and _is_fresh(*cached):
        archive, meta = cached
        progress(100, f"Используется загруженный файл: {archive.name}")
        return DepAcquisition(key, archive, str(meta.get("version") or ""), True)
    client = SiteClient()
    version = ""
    try:
        progress(None, f"Загрузка зависимости: {dep.name}")
        if key == "shv":
            downloaded, version = client.download_scripthookv(
                progress=progress, cancel=cancel, dest_dir=_cache_dir(key)
            )
        elif key in GITHUB_DEPS:
            downloaded, version = client.download_github_dependency(
                key, progress=progress, cancel=cancel, dest_dir=_cache_dir(key)
            )
        else:
            downloaded = client.download_gta5mods_tool(key, dep.url, progress=progress, cancel=cancel)
        _cleanup_cache_duplicates(key, downloaded.path)
        _write_cache_meta(
            _cache_dir(key),
            {
                "filename": downloaded.filename,
                "url": downloaded.url,
                "version": version,
                "size": downloaded.size,
                "downloaded_at": time.time(),
            },
        )
        return DepAcquisition(key, downloaded.path, version, False)
    except DownloadCancelled:
        raise
    except SiteError as exc:
        if cached and not force:
            archive, meta = cached
            warning = f"Не удалось обновить {dep.name}: {exc}. Использован ранее загруженный файл."
            progress(None, warning)
            return DepAcquisition(key, archive, str(meta.get("version") or ""), True, warning)
        raise
    finally:
        client.close()


def _copy_selected(files: list[Path], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for source in files:
        if not source.is_file():
            continue
        destination = target / source.name
        try:
            shutil.copy2(source, destination)
        except OSError:
            pass


def _filtered_staging(key: str, archive: Path, extracted: Path, root: Path) -> list[str]:
    from .archives import content_root

    notes: list[str] = []
    if key == "shv":
        loader = _find_named(extracted, "dinput8", suffix=".dll") or _find_named(
            extracted, "xinput1_4", suffix=".dll"
        )
        library = _find_named(extracted, "scripthookv", suffix=".dll")
        if loader is None or library is None:
            inner = content_root(extracted, aggressive=True)
            loader = loader or _find_named(inner, "dinput8", suffix=".dll")
            library = library or _find_named(inner, "scripthookv", suffix=".dll")
        if loader is not None and library is not None:
            selected = {loader.resolve(), library.resolve()}
            _copy_selected([Path(item) for item in selected], root)
            notes.append(
                "Script Hook V: установлены только dinput8.dll и ScriptHookV.dll. "
                "Демонстрационный NativeTrainer.asi и альтернативный загрузчик пропущены."
            )
        return notes
    if key == "shvdn":
        candidates = [
            entry
            for entry in _walk(extracted)
            if entry.name.lower().startswith("scripthookvdotnet")
            and entry.suffix.lower() in {".asi", ".dll", ".ini", ".xml"}
        ]
        _copy_selected(candidates, root)
        return notes
    if key == "lemonui":
        folder = _find_folder(extracted, "shvdn3")
        files: list[Path]
        if folder is not None:
            files = [entry for entry in _walk(folder) if entry.suffix.lower() in {".dll", ".xml"}]
        else:
            files = [
                entry
                for entry in _walk(extracted)
                if "lemonui" in entry.name.lower() and "shvdn3" in entry.name.lower()
            ]
        _copy_selected(files, root / "scripts")
        notes.append("LemonUI: выбран вариант для Script Hook V .NET 3 (SHVDN3).")
        return notes
    if key == "nativeui":
        files = [
            entry
            for entry in _walk(extracted)
            if entry.name.lower() in {"nativeui.dll", "nativeui.xml"}
        ]
        _copy_selected(files, root / "scripts")
        return notes
    if key == "lua":
        base = content_root(extracted, aggressive=True)
        for entry in _walk(base):
            relative = entry.relative_to(base)
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(entry, destination)
            except OSError:
                pass
        notes.append("Lua Plugin: установлены LUA.asi и стандартные скрипты из архива.")
        return notes
    if key in {"heap", "packfile"}:
        files = [
            entry
            for entry in _walk(extracted)
            if entry.suffix.lower() in {".asi", ".ini"}
        ]
        _copy_selected(files, root)
        return notes
    base = content_root(extracted, aggressive=True)
    for entry in _walk(base):
        relative = entry.relative_to(base)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(entry, destination)
        except OSError:
            pass
    return notes


def _walk(directory: Path) -> list[Path]:
    result: list[Path] = []
    try:
        for entry in directory.rglob("*"):
            if entry.is_file() and "__MACOSX" not in entry.parts:
                result.append(entry)
    except OSError:
        pass
    return result


def _find_folder(directory: Path, name: str) -> Path | None:
    target = name.lower()
    try:
        for entry in directory.rglob("*"):
            if entry.is_dir() and entry.name.lower() == target:
                return entry
    except OSError:
        pass
    return None


def prepare_dependency_source(
    key: str,
    *,
    progress: ProgressCB = noop_progress,
    cancel=None,
    force: bool = False,
) -> tuple[PreparedSource, DepAcquisition]:
    dep = DEP_BY_KEY.get(key)
    if dep is None:
        raise SiteError(f"Неизвестная зависимость: {key}")
    acquisition = acquire_dependency(key, progress=progress, cancel=cancel, force=force)
    work = STAGING_DIR / f"dep-{key}-{uuid.uuid4().hex[:8]}"
    extracted = work / "extracted"
    root = work / "root"
    extracted.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        from .archives import extract_archive

        progress(None, f"Распаковка: {dep.name}")
        extract_archive(acquisition.archive, extracted, progress)
        notes = _filtered_staging(key, acquisition.archive, extracted, root)
        source = prepare_source(
            root,
            progress,
            meta={
                "title": dep.name,
                "page_url": dep.url,
                "author": "",
                "description": "",
                "deps": [],
            },
        )
        source.folder_label = ""
        source.temp_dirs.append(work)
        source.warnings.extend(notes)
        if acquisition.warning:
            source.warnings.append(acquisition.warning)
        return source, acquisition
    except Exception:
        remove_tree(work)
        raise


def clear_dependency_cache(key: str | None = None) -> None:
    base = DOWNLOADS_DIR / "deps"
    targets = [_cache_dir(key)] if key else [entry for entry in base.iterdir() if entry.is_dir()] if base.is_dir() else []
    for directory in targets:
        remove_tree(directory)
