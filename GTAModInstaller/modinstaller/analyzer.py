from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .deps_defs import dep_name
from .oiv import OivPackage, parse_oiv
from .staging import PreparedSource
from .utils import ProgressCB, human_size, hidden_or_junk, noop_progress, safe_slug, sanitize_dest_rel

KIND_ASI = "asi"
KIND_SCRIPT = "script"
KIND_CORE = "core"
KIND_MODS = "mods"
KIND_DLC = "dlc"
KIND_LML = "lml"
KIND_OPENIV = "openiv"
KIND_ROOT = "root"
KIND_CONFIG = "config"
KIND_MANUAL = "manual"

SHV = "shv"
SHVDN = "shvdn"
LEMONUI = "lemonui"
NATIVEUI = "nativeui"
OPENIV = "openiv"
LML = "lml"
LUA = "lua"
HEAP = "heap"
PACKFILE = "packfile"
GAMECONFIG = "gameconfig"

SCRIPT_EXTS = {".dll", ".cs", ".vb", ".lua"}
SHVDN_EXTS = {".dll", ".cs", ".vb"}
ASI_EXT = ".asi"
VANILLA_ROOTS = {
    "update",
    "x64",
    "x64a",
    "x64b",
    "x64c",
    "x64d",
    "x64e",
    "x64f",
    "x64g",
    "x64h",
    "x64i",
    "common",
    "platform",
}
SCRIPT_DATA_EXTS = {
    ".rpf",
    ".ytd",
    ".ydr",
    ".yft",
    ".ymap",
    ".ytyp",
    ".ybn",
    ".ydd",
    ".ycd",
    ".awc",
    ".meta",
    ".rel",
    ".nametables",
    ".gxt2",
}
EXECUTABLE_EXTS = {".exe", ".msi", ".bat", ".cmd", ".vbs", ".ps1", ".jar", ".scr"}
CONFIG_EXTS = {".ini", ".xml", ".json", ".txt", ".log", ".cfg", ".yaml", ".yml", ".toml"}
RESOURCE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".dds",
    ".ico",
    ".wav",
    ".ogg",
    ".mp3",
    ".ttf",
    ".otf",
    ".dat",
    ".bin",
    ".pak",
    ".db",
}
DEV_DIRS = {"src", "source", "sources", "developer", "developers", "srcfiles"}
DOC_DIRS = {"screenshots", "screenshot", "preview", "previews", "docs", "documentation", "media", "gallery"}
DOC_README_RE = re.compile(
    r"^(readme|read me|install|installation|instructions?|как\s+установить|установка|guide|manual|notes)\b.*\.(txt|md|nfo|rtf|html?)$",
    re.IGNORECASE,
)
DOC_OTHER_RE = re.compile(
    r"^(license|licence|changelog|changes|credits|thanks|version|versions|copying|eula|terms)\b.*\.(txt|md|nfo|pdf|rtf|html?|docx?)$",
    re.IGNORECASE,
)
SCREENSHOT_RE = re.compile(r"(screenshot|preview|thumbnail|banner|promo)", re.IGNORECASE)

CORE_ROOT = {
    "dinput8.dll",
    "scripthookv.dll",
    "scripthookvdotnet.asi",
    "scripthookvdotnet.ini",
    "scripthookvdotnet2.dll",
    "scripthookvdotnet3.dll",
    "scripthookvdotnet2.xml",
    "scripthookvdotnet3.xml",
    "openiv.asi",
}
UI_SCRIPT_NAMES = {"nativeui.dll", "nativeui.xml", "nativeui.pdb"}


@dataclass
class PlanItem:
    source_id: str
    source_title: str
    src: Path
    dest_rel: str
    kind: str
    note: str = ""
    enabled: bool = True
    manual: bool = False
    size: int = 0
    conflict: bool = False
    existing_size: int = 0
    action: str = "add"

    @property
    def file_name(self) -> str:
        return self.src.name


@dataclass
class IgnoredItem:
    rel: str
    reason: str


@dataclass
class ReadmeDoc:
    name: str
    rel: str
    text: str


@dataclass
class SourcePlan:
    source: PreparedSource
    items: list[PlanItem] = field(default_factory=list)
    ignored: list[IgnoredItem] = field(default_factory=list)
    deps: set[str] = field(default_factory=set)
    dep_reasons: dict[str, list[str]] = field(default_factory=dict)
    provided: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    readmes: list[ReadmeDoc] = field(default_factory=list)
    dlc_names: list[str] = field(default_factory=list)
    oiv_packages: list[OivPackage] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.source.title

    @property
    def enabled_items(self) -> list[PlanItem]:
        return [item for item in self.items if item.enabled]

    @property
    def manual_items(self) -> list[PlanItem]:
        return [item for item in self.items if item.manual]

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.items if item.enabled)

    @property
    def conflict_count(self) -> int:
        return sum(1 for item in self.items if item.enabled and item.conflict)

    @property
    def installable(self) -> bool:
        return any(item.enabled and not item.manual for item in self.items)

    def add_dep(self, key: str, reason: str) -> None:
        if not key:
            return
        self.deps.add(key)
        reasons = self.dep_reasons.setdefault(key, [])
        if reason and reason not in reasons:
            reasons.append(reason)


@dataclass
class _Anchor:
    base: str
    directory: Path


def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _is_doc(rel: Path, is_readme: bool = False) -> str | None:
    name = rel.name
    lowered_parts = {part.lower() for part in rel.parts[:-1]}
    if DOC_README_RE.match(name):
        return "readme"
    if DOC_OTHER_RE.match(name):
        return "doc"
    if rel.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        if lowered_parts & DOC_DIRS or SCREENSHOT_RE.search(name):
            return "screenshot"
    if lowered_parts & {"screenshots", "preview", "docs", "documentation", "media", "gallery"}:
        return "doc"
    if is_readme:
        return "readme"
    return None


def _is_dev_source(rel: Path) -> bool:
    lowered = {part.lower() for part in rel.parts[:-1]}
    if lowered & DEV_DIRS:
        return rel.suffix.lower() in {".cs", ".vb", ".sln", ".csproj", ".vbproj", ".suo", ".user", ".rs", ".cpp", ".h"}
    return False


def _direct_files(directory: Path) -> list[Path]:
    try:
        return [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return []


def _directory_anchor(directory: Path) -> _Anchor | None:
    files = _direct_files(directory)
    asi_present = False
    script_present = False
    for file in files:
        name = file.name.lower()
        suffix = file.suffix.lower()
        if suffix == ASI_EXT:
            if name not in CORE_ROOT:
                asi_present = True
        elif suffix in SCRIPT_EXTS:
            if name not in CORE_ROOT and not name.startswith("scripthookvdotnet"):
                script_present = True
        elif name in UI_SCRIPT_NAMES:
            script_present = True
    if asi_present:
        return _Anchor("", directory)
    if script_present:
        return _Anchor("scripts", directory)
    return None


def _anchor_for(path: Path, root: Path, cache: dict[Path, _Anchor | None]) -> _Anchor | None:
    chain: list[Path] = []
    current = path.parent
    anchor: _Anchor | None = None
    while True:
        if current in cache:
            anchor = cache[current]
            break
        chain.append(current)
        found = _directory_anchor(current)
        if found:
            anchor = found
            cache[current] = found
            break
        if current == root:
            cache[current] = None
            break
        try:
            current = current.parent
        except OSError:
            break
    for directory in chain:
        cache.setdefault(directory, anchor)
    return anchor


SCRIPT_WRAPPER_NAMES = {
    "toyourscriptfolder",
    "toyourscriptsfolder",
    "putinscriptfolder",
    "putinscriptsfolder",
    "putinyourscriptsfolder",
    "putinscripts",
    "putinyourscripts",
    "copyintoscripts",
    "copytoscripts",
    "copyintoscriptsfolder",
    "copytoscriptsfolder",
    "movetoscripts",
    "movetoscriptsfolder",
    "placetoscripts",
    "placetoscriptsfolder",
    "scriptfolder",
    "scriptsfolder",
    "впапкуscripts",
    "впапкускриптов",
    "скопироватьвscripts",
    "скопироватьвскрипты",
}


def _is_script_wrapper(name: str) -> bool:
    compact = re.sub(r"[^0-9a-zа-яё]+", "", name.lower())
    return compact in SCRIPT_WRAPPER_NAMES


def _scripts_layout(parts: tuple[str, ...] | list[str]) -> str:
    cleaned = [part for part in parts if part not in {"", "."}]
    wrappers = [index for index, part in enumerate(cleaned[:-1]) if _is_script_wrapper(part)]
    if wrappers:
        cleaned = cleaned[wrappers[-1] + 1 :]
    lowered = [part.lower() for part in cleaned[:-1]]
    if "scripts" in lowered:
        cleaned = cleaned[lowered.index("scripts") + 1 :]
    return "scripts/" + "/".join(cleaned) if cleaned else "scripts"


def _anchor_folder(directory: Path, root: Path, source: PreparedSource) -> str:
    try:
        relative = directory.relative_to(root)
    except ValueError:
        relative = Path()
    parts = [part for part in relative.parts if part not in {"", "."}]
    wrappers = [index for index, part in enumerate(parts) if _is_script_wrapper(part)]
    if wrappers:
        return ""
    if parts and parts[0].lower() == "scripts":
        parts = parts[1:]
    if parts:
        return sanitize_dest_rel("/".join(parts)) or ""
    label = sanitize_dest_rel(source.folder_label) or ""
    if label.lower() in {"scripts", "mods", "lml", "update", "dlcpacks"} or _is_script_wrapper(label):
        return ""
    return label


def _scripts_target(path: Path, root: Path, anchor: _Anchor, source: PreparedSource) -> str:
    try:
        parts = list(path.relative_to(root).parts)
    except ValueError:
        parts = [path.name]
    if "scripts" in [part.lower() for part in parts[:-1]]:
        return _scripts_layout(parts)
    try:
        inner = list(path.relative_to(anchor.directory).parts)
    except ValueError:
        inner = [path.name]
    folder = _anchor_folder(anchor.directory, root, source)
    return "scripts/" + "/".join(([folder] if folder else []) + inner)


def _known_core_dest(name: str) -> tuple[str, str, str, str] | None:
    """Returns (dest_rel, kind, dep_hint, note) for known loader/UI files."""
    lowered = name.lower()
    suffix = Path(lowered).suffix
    if lowered in {"dinput8.dll", "scripthookv.dll", "openiv.asi"}:
        note = "Компонент ASI Loader / Script Hook V" if lowered != "openiv.asi" else "Плагин OpenIV ASI"
        return name, KIND_CORE, SHV, note
    if lowered.startswith("scripthookvdotnet") and suffix in {".dll", ".asi", ".ini", ".xml"}:
        return name, KIND_CORE, SHVDN, "Компонент Script Hook V .NET"
    if lowered == "lua.asi":
        return name, KIND_CORE, LUA, "Lua Plugin (ASI)"
    if lowered.startswith("heapadjuster."):
        return name, KIND_CORE, HEAP, "Heap Adjuster"
    if lowered.startswith("packfilelimitadjuster."):
        return name, KIND_CORE, PACKFILE, "Packfile Limit Adjuster"
    if lowered.startswith("lemonui."):
        if "shvdn" in lowered:
            return f"scripts/{name}", KIND_SCRIPT, LEMONUI, "Библиотека LemonUI для SHVDN"
        return None
    if lowered in UI_SCRIPT_NAMES:
        return f"scripts/{name}", KIND_SCRIPT, NATIVEUI, "Библиотека NativeUI"
    return None


def _looks_like_dlc_path(rel: Path) -> str | None:
    parts = list(rel.parts)
    lowered = [part.lower() for part in parts]
    if "dlcpacks" in lowered:
        index = lowered.index("dlcpacks")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def _scan_binary_deps(path: Path) -> set[str]:
    try:
        with open(path, "rb") as fh:
            data = fh.read(6 * 1024 * 1024).lower()
    except OSError:
        return set()
    found: set[str] = set()
    if b"lemonui" in data:
        found.add(LEMONUI)
    if b"nativeui" in data:
        found.add(NATIVEUI)
    if b"scripthookvdotnet" in data:
        found.add(SHVDN)
    return found


def _text_dep_hints(text: str) -> set[str]:
    lowered = text.lower()
    found: set[str] = set()
    patterns = {
        SHV: (r"script\s*hook\s*v(?!\s*\.?\s*net)", r"scripthookv\b(?!dotnet)", r"scripthook\s*v\b"),
        SHVDN: (r"scripthookvdotnet", r"script\s*hook\s*v\s*\.?\s*net", r"\bshvdn\b", r"\.net\s+script"),
        LEMONUI: (r"lemonui",),
        NATIVEUI: (r"nativeui",),
        OPENIV: (r"openiv",),
        LML: (r"lenny'?s?\s+mod\s+loader", r"\blml\b"),
        LUA: (r"lua\s+plugin", r"lua\.asi\b", r"lua\s+scripts?"),
        HEAP: (r"heap\s*adjuster", r"heapadjuster"),
        PACKFILE: (r"packfile\s+limit\s+adjuster", r"packfilelimitadjuster", r"packfile\s+limit"),
        GAMECONFIG: (r"gameconfig", r"game\s+config"),
    }
    for key, regexes in patterns.items():
        if any(re.search(regex, lowered) for regex in regexes):
            found.add(key)
    return found


def _dest_from_loose_target(target: str) -> str | None:
    return sanitize_dest_rel(target)


class Analyzer:
    def __init__(self, game_path: Path | str | None = None):
        self.game_path = Path(game_path) if game_path else None

    def analyze(self, source: PreparedSource, progress: ProgressCB = noop_progress) -> SourcePlan:
        plan = SourcePlan(source=source)
        plan.warnings.extend(source.warnings)
        progress(0, f"Анализ: {source.title}")
        anchors: dict[Path, _Anchor | None] = {}
        readme_hints: set[str] = set()

        roots: list[tuple[Path, list[Path]]] = []
        total_files = 0
        for root in source.roots:
            files: list[Path] = []
            for base, dirs, names in os.walk(root):
                dirs[:] = [
                    name
                    for name in dirs
                    if name.lower() not in {"__macosx", ".git", ".svn", ".vs", "__pycache__"}
                ]
                for name in names:
                    path = Path(base) / name
                    if path in source.consumed:
                        plan.ignored.append(
                            IgnoredItem(_rel_posix(path, root), "Вложенный архив уже распакован")
                        )
                        continue
                    rel = path.relative_to(root)
                    if hidden_or_junk(rel):
                        continue
                    files.append(path)
            roots.append((root, files))
            total_files += len(files)

        # DLC folders that are not already a game-relative mods/update path.
        dlc_mappings: dict[Path, str] = {}
        dlc_names: set[str] = set()
        for root, files in roots:
            for path in files:
                if path.name.lower() != "dlc.rpf":
                    continue
                rel = path.relative_to(root)
                name = _looks_like_dlc_path(rel)
                if name:
                    dlc_names.add(name)
                    continue
                parent = path.parent
                pack_name = parent.name if parent != root else safe_slug(source.title, "dlcpack")
                if not pack_name or pack_name.lower() in {"update", "mods"}:
                    pack_name = safe_slug(source.title, "dlcpack")
                pack_name = sanitize_dest_rel(pack_name) or "dlcpack"
                pack_name = pack_name.replace("/", "_")
                dlc_mappings[parent] = pack_name
                dlc_names.add(pack_name)

        processed = 0
        seen_destinations: dict[str, PlanItem] = {}

        def emit() -> None:
            if total_files:
                progress(min(99, int(processed / total_files * 92)), f"Анализ файлов: {source.title}")

        def add_item(
            path: Path,
            dest_rel: str | None,
            kind: str,
            note: str = "",
            enabled: bool = True,
            action: str = "add",
        ) -> None:
            if not dest_rel:
                plan.ignored.append(IgnoredItem(path.name, "Некорректный путь назначения"))
                return
            clean = sanitize_dest_rel(dest_rel)
            if not clean:
                plan.ignored.append(IgnoredItem(path.name, "Некорректный путь назначения"))
                return
            item = PlanItem(
                source_id=source.source_id,
                source_title=source.title,
                src=path,
                dest_rel=clean,
                kind=kind,
                note=note,
                enabled=enabled and kind != KIND_MANUAL,
                manual=(kind == KIND_MANUAL) or not enabled,
                size=_safe_size(path),
                action=action,
            )
            plan.items.append(item)
            key = clean.lower()
            previous = seen_destinations.get(key)
            if previous is not None:
                try:
                    plan.items.remove(previous)
                except ValueError:
                    pass
                plan.ignored.append(IgnoredItem(previous.src.name, "Дубликат пути назначения"))
                plan.warnings.append(f"Дубликат файла в пакете: {clean} — оставлен последний.")
            seen_destinations[key] = item

        def add_manual(path: Path, note: str, rel_display: str | None = None) -> None:
            del rel_display
            plan.items.append(
                PlanItem(
                    source_id=source.source_id,
                    source_title=source.title,
                    src=path,
                    dest_rel="",
                    kind=KIND_MANUAL,
                    note=note,
                    enabled=False,
                    manual=True,
                    size=_safe_size(path),
                )
            )

        def classify_file(root: Path, path: Path) -> None:
            nonlocal processed, readme_hints
            processed += 1
            if processed % 60 == 0:
                emit()
            rel = path.relative_to(root)
            rel_posix = rel.as_posix()
            name = path.name
            lowered = name.lower()
            suffix = path.suffix.lower()

            if _is_dev_source(rel):
                plan.ignored.append(IgnoredItem(rel_posix, "Исходники разработчика"))
                return

            doc_type = _is_doc(rel)
            if doc_type:
                if doc_type == "readme":
                    text = _read_readme(path)
                    if text:
                        plan.readmes.append(ReadmeDoc(name, rel_posix, text))
                        readme_hints.update(_text_dep_hints(text))
                plan.ignored.append(
                    IgnoredItem(rel_posix, "Инструкция" if doc_type == "readme" else "Документация/скриншоты")
                )
                return

            # Files inside a top-level add-on DLC folder.
            for parent, pack_name in dlc_mappings.items():
                try:
                    relative = path.relative_to(parent).as_posix()
                except ValueError:
                    continue
                dest = f"mods/update/x64/dlcpacks/{pack_name}/{relative}"
                if relative.lower().endswith("dlc.rpf"):
                    note = f"Add-on DLC «{pack_name}». Активируйте через dlclist.xml"
                else:
                    note = f"Файлы add-on DLC «{pack_name}»"
                add_item(path, dest, KIND_DLC, note)
                return

            known = _known_core_dest(name)
            if known:
                dest, kind, dep_hint, note = known
                add_item(path, dest, kind, note)
                if dep_hint:
                    plan.add_dep(dep_hint, f"В комплекте: {name}")
                return

            first = rel.parts[0].lower() if rel.parts else ""
            if suffix == ASI_EXT:
                add_item(path, name, KIND_ASI, "ASI-мод → корень игры (через Script Hook V)")
                plan.add_dep(SHV, f"ASI-мод: {name}")
                return

            if first == "scripts":
                add_item(path, rel_posix, KIND_SCRIPT, "Готовая структура scripts")
                if suffix in SHVDN_EXTS:
                    plan.add_dep(SHVDN, f"SHVDN-скрипт: {name}")
                if suffix == ".lua":
                    plan.add_dep(LUA, f"Lua-скрипт: {name}")
                return
            if first == "mods":
                add_item(path, rel_posix, KIND_MODS, "Готовая структура mods")
                if suffix == ".rpf":
                    plan.add_dep(OPENIV, "Мод для папки mods")
                return
            if first == "lml":
                add_item(path, rel_posix, KIND_LML, "Lenny's Mod Loader")
                plan.add_dep(LML, "Структура lml")
                return
            if first == "dlcpacks":
                add_item(path, f"mods/update/x64/{rel_posix}", KIND_DLC, "Add-on DLC")
                plan.add_dep(OPENIV, "Add-on DLC (dlclist.xml)")
                return
            if first in VANILLA_ROOTS or (len(rel.parts) == 1 and suffix == ".rpf"):
                add_item(path, f"mods/{rel_posix}", KIND_OPENIV, "Игровой RPF → папка mods (нужен OpenIV)")
                plan.add_dep(OPENIV, f"RPF-файл: {name}")
                return
            if suffix == ".rpf":
                add_item(path, f"mods/{rel_posix}", KIND_OPENIV, "RPF-архив → папка mods")
                plan.add_dep(OPENIV, f"RPF-файл: {name}")
                return
            if suffix in EXECUTABLE_EXTS:
                add_manual(
                    path,
                    "Исполняемый файл не запускается автоматически. Установите вручную только если доверяете источнику.",
                    rel_posix,
                )
                return
            if suffix == ".oiv":
                add_manual(path, "OIV-пакет не удалось разобрать. Откройте его через OpenIV.", rel_posix)
                plan.add_dep(OPENIV, "OIV-пакет требует OpenIV")
                return
            if suffix == ".sln" or suffix in {".csproj", ".vbproj"} or suffix == ".nupkg":
                plan.ignored.append(IgnoredItem(rel_posix, "Файл проекта разработчика"))
                return
            if suffix == ".url" or suffix == ".lnk":
                plan.ignored.append(IgnoredItem(rel_posix, "Ссылка/ярлык"))
                return
            if any(part.lower() in {"altv", "fivem", "rph", "ragemp", "shvdnc", "for developers"} for part in rel.parts[:-1]):
                plan.ignored.append(IgnoredItem(rel_posix, "Файлы для другой платформы"))
                return
            if name.lower().startswith("lemonui.") and "shvdn" not in name.lower():
                plan.ignored.append(IgnoredItem(rel_posix, "LemonUI для другой платформы"))
                return

            anchor = _anchor_for(path, root, anchors)
            if anchor:
                relative = path.relative_to(anchor.directory).as_posix()
                if suffix in SHVDN_EXTS:
                    if suffix == ".dll" and anchor.base == "":
                        if path.parent == anchor.directory:
                            dest = relative
                            kind = KIND_ROOT
                            note = "DLL рядом с ASI-модом (в корень игры)"
                        else:
                            dest = relative
                            kind = KIND_ROOT
                            note = "DLL-зависимость ASI-мода"
                    else:
                        dest = _scripts_target(path, root, anchor, source)
                        kind = KIND_SCRIPT
                        note = "SHVDN-скрипт → scripts"
                    add_item(path, dest, kind, note)
                    plan.add_dep(SHVDN, f"Скрипт: {name}")
                    return
                if suffix == ".lua":
                    if anchor.base == "scripts":
                        add_item(
                            path,
                            _scripts_target(path, root, anchor, source),
                            KIND_SCRIPT,
                            "Lua-скрипт → scripts",
                        )
                    else:
                        add_item(path, f"scripts/{path.name}", KIND_SCRIPT, "Lua-скрипт → scripts")
                    plan.add_dep(LUA, f"Lua-скрипт: {name}")
                    return
                if anchor.base == "scripts":
                    add_item(
                        path,
                        _scripts_target(path, root, anchor, source),
                        KIND_SCRIPT,
                        "Ресурс рядом со скриптом",
                    )
                else:
                    add_item(path, relative, KIND_ROOT, "Ресурс рядом со скриптом")
                return

            if suffix in CONFIG_EXTS:
                folder = _anchor_folder(root, root, source)
                if len(rel.parts) > 1:
                    add_item(
                        path,
                        _scripts_layout(rel.parts),
                        KIND_SCRIPT,
                        "Файл конфигурации мода → scripts",
                    )
                elif folder:
                    add_item(
                        path,
                        f"scripts/{folder}/{rel_posix}",
                        KIND_SCRIPT,
                        "Файл конфигурации мода → scripts",
                    )
                else:
                    add_item(path, rel_posix, KIND_CONFIG, "Файл конфигурации → корень игры")
                return
            if suffix in RESOURCE_EXTS:
                if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
                    add_manual(path, "Изображение без скрипта. Установите вручную при необходимости.", rel_posix)
                else:
                    add_manual(path, "Ресурс без скрипта — не удалось определить папку назначения.", rel_posix)
                return
            if suffix in SCRIPT_DATA_EXTS:
                add_manual(
                    path,
                    "Файл для RPF (OpenIV): замените его вручную в соответствующем архиве.",
                    rel_posix,
                )
                plan.add_dep(OPENIV, f"Файл RPF: {name}")
                return
            if suffix in {".rar", ".zip", ".7z"}:
                add_manual(path, "Вложенный архив не распакован. Извлеките его вручную.", rel_posix)
                return
            add_manual(path, "Не удалось определить назначение файла. Проверьте инструкцию мода.", rel_posix)

        for root, files in roots:
            files.sort(key=lambda p: str(p).lower())
            for path in files:
                classify_file(root, path)
            emit()

        # OIV packages
        for oiv_root in source.oiv_roots:
            package = parse_oiv(oiv_root, source.title)
            plan.oiv_packages.append(package)
            if package.title and not source.meta.get("title"):
                pass
            plan.warnings.extend(package.warnings)
            if package.requires_openiv:
                plan.add_dep(OPENIV, "OIV-пакет")
                actions = package.openiv_actions[:3]
                count = len(package.openiv_actions)
                summary = "; ".join(actions)
                if count > 3:
                    summary += f" и ещё {count - 3}"
                plan.warnings.append(
                    "OIV содержит операции, которые выполняет только OpenIV"
                    + (f": {summary}" if summary else ".")
                )
            for entry in package.files:
                dest = _dest_from_loose_target(entry.dest_rel)
                if not dest:
                    plan.warnings.append(f"OIV: пропущен недопустимый путь {entry.dest_rel}")
                    continue
                suffix = Path(dest).suffix.lower()
                if suffix == ASI_EXT:
                    kind = KIND_ASI
                elif suffix in SHVDN_EXTS or suffix == ".lua":
                    kind = KIND_SCRIPT
                elif dest.lower().startswith("scripts/"):
                    kind = KIND_SCRIPT
                elif dest.lower().startswith("lml/"):
                    kind = KIND_LML
                else:
                    kind = KIND_CONFIG
                add_item(entry.src, dest, kind, f"OIV · {entry.action}", action=entry.action)
                if kind == KIND_ASI:
                    plan.add_dep(SHV, "OIV: ASI-файл")
                if suffix in SHVDN_EXTS:
                    plan.add_dep(SHVDN, "OIV: SHVDN-скрипт")
                if dest.lower().startswith("lml/"):
                    plan.add_dep(LML, "OIV: lml")

        if readme_hints:
            for key in readme_hints:
                plan.add_dep(key, "Упоминание в README")

        for key, reason in _page_deps(plan.source):
            plan.add_dep(key, reason)

        # Scan scripts for library references.
        scanned = 0
        for item in plan.items:
            if scanned >= 90:
                break
            if item.src.suffix.lower() not in {".dll", ".asi", ".cs", ".vb"}:
                continue
            if item.kind == KIND_MANUAL:
                continue
            scanned += 1
            for dep in _scan_binary_deps(item.src):
                plan.add_dep(dep, f"Ссылка в {item.src.name}")

        if any(item.kind in (KIND_OPENIV, KIND_DLC) for item in plan.items):
            plan.add_dep(OPENIV, "Мод изменяет RPF/DLC")

        plan.dlc_names = sorted(dlc_names)
        for name in plan.dlc_names:
            plan.warnings.append(
                f"DLC «{name}»: добавьте <Item>dlcpacks:/{name}/</Item> в dlclist.xml через OpenIV."
            )

        manual_count = sum(1 for item in plan.items if item.manual)
        if manual_count:
            plan.warnings.append(
                f"Файлов для ручной установки: {manual_count} — они отключены в плане."
            )

        plan.provided = _provided_dependencies(plan)

        # Remove dependencies that are satisfied by files included in the package.
        plan.deps = {dep for dep in plan.deps if dep}
        plan.items.sort(key=lambda item: (item.manual, item.dest_rel.lower(), item.file_name.lower()))
        self._mark_conflicts(plan)
        progress(100, "Анализ завершён")
        return plan

    def _mark_conflicts(self, plan: SourcePlan) -> None:
        if not self.game_path:
            return
        for item in plan.items:
            if not item.enabled:
                continue
            target = self.game_path / item.dest_rel
            if target.exists():
                item.conflict = True
                try:
                    item.existing_size = target.stat().st_size if target.is_file() else 0
                except OSError:
                    item.existing_size = 0


def _provided_dependencies(plan: SourcePlan) -> set[str]:
    provided: set[str] = set()
    for item in plan.items:
        if not item.enabled:
            continue
        name = item.src.name.lower()
        if name in {"dinput8.dll", "scripthookv.dll"}:
            provided.add(SHV)
        if name.startswith("scripthookvdotnet"):
            provided.add(SHVDN)
        if name.startswith("lemonui") and "shvdn" in name:
            provided.add(LEMONUI)
        if name == "nativeui.dll":
            provided.add(NATIVEUI)
        if name == "lua.asi":
            provided.add(LUA)
        if name.startswith("heapadjuster."):
            provided.add(HEAP)
        if name.startswith("packfilelimitadjuster."):
            provided.add(PACKFILE)
        if item.kind == KIND_LML:
            provided.add(LML)
    return provided


def _page_deps(source: PreparedSource) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for key in source.meta.get("deps") or []:
        if isinstance(key, str):
            found.add((key, "Указано на странице мода"))
    description = source.description
    if description:
        for key in _text_dep_hints(description):
            found.add((key, "Описание на странице мода"))
    return found


def _read_readme(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > 300_000:
        return ""
    from .utils import read_text_limited, strip_html

    text = read_text_limited(path, 200_000)
    if path.suffix.lower() in {".html", ".htm"}:
        text = strip_html(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:25_000]


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def apply_game_context(plans: list[SourcePlan], game_path: Path | str | None) -> None:
    game = Path(game_path) if game_path else None
    for plan in plans:
        for item in plan.items:
            item.conflict = False
            item.existing_size = 0
            if not game or not item.enabled:
                continue
            target = game / item.dest_rel
            try:
                if target.exists():
                    item.conflict = True
                    if target.is_file():
                        item.existing_size = target.stat().st_size
            except OSError:
                pass


def plan_summary(plans: list[SourcePlan]) -> tuple[int, int, int, int]:
    files = replaced = manual = 0
    size = 0
    for plan in plans:
        for item in plan.items:
            if item.manual:
                manual += 1
                continue
            if item.enabled:
                files += 1
                size += item.size
                if item.conflict:
                    replaced += 1
    return files, size, replaced, manual


def dependencies_missing(plans: list[SourcePlan]) -> dict[str, list[str]]:
    needed: dict[str, list[str]] = {}
    for plan in plans:
        for key in plan.deps:
            if key in plan.provided:
                continue
            reasons = plan.dep_reasons.get(key, [])
            bucket = needed.setdefault(key, [])
            for reason in reasons:
                if reason not in bucket:
                    bucket.append(reason)
            if not reasons and dep_name(key) not in bucket:
                bucket.append(plan.title)
    return needed
