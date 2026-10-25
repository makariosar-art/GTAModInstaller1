from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .utils import is_subpath, strip_html

LOOSE_PREFIXES = ("scripts/", "lml/", "mods/")
LOOSE_EXTS = {
    ".asi",
    ".dll",
    ".cs",
    ".vb",
    ".lua",
    ".ini",
    ".xml",
    ".json",
    ".txt",
    ".cfg",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
    ".dds",
    ".wav",
    ".ogg",
}


@dataclass
class OivFile:
    src: Path
    dest_rel: str
    action: str = "add"


@dataclass
class OivPackage:
    root: Path
    title: str
    files: list[OivFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_openiv: bool = False
    openiv_actions: list[str] = field(default_factory=list)


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return strip_html("".join(element.itertext()))


def _find_assembly(root: Path) -> Path | None:
    direct = root / "assembly.xml"
    if direct.is_file():
        return direct
    try:
        for entry in root.iterdir():
            if entry.is_dir():
                candidate = entry / "assembly.xml"
                if candidate.is_file():
                    return candidate
        for path in root.rglob("assembly.xml"):
            if path.is_file():
                return path
    except OSError:
        pass
    return None


def _normalize_target(value: str) -> str | None:
    value = (value or "").strip().strip('"')
    value = value.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    lowered = value.lower()
    for prefix in ("grand theft auto v/", "gta v/", "gtav/"):
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            lowered = value.lower()
    if not value or value.startswith("/") or ".." in value.split("/") or ":" in value.split("/")[0]:
        return None
    return value


def _looks_loose(target: str) -> bool:
    lowered = target.lower()
    if ".rpf/" in lowered or ".rpf\\" in lowered or lowered.endswith(".rpf"):
        return False
    if lowered.startswith(LOOSE_PREFIXES):
        return True
    if "/" not in target:
        suffix = Path(target).suffix.lower()
        return suffix in LOOSE_EXTS
    return False


def _source_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    if source.is_dir():
        files: list[Path] = []
        for base, dirs, names in os.walk(source):
            dirs[:] = [name for name in dirs if name.lower() not in {"__macosx", ".git"}]
            for name in names:
                files.append(Path(base) / name)
        return files
    return []


def parse_oiv(root: Path, fallback_title: str = "OIV-пакет") -> OivPackage:
    assembly = _find_assembly(root)
    package = OivPackage(root=root, title=fallback_title)
    if not assembly:
        package.warnings.append("В OIV-пакете не найден assembly.xml.")
        return package
    try:
        tree = ET.parse(assembly)
    except (ET.ParseError, OSError) as exc:
        package.warnings.append(f"Не удалось прочитать assembly.xml: {exc}")
        return package
    document = tree.getroot()
    base = assembly.parent
    metadata = document.find("metadata")
    if metadata is not None:
        title = _text(metadata.find("name"))
        if title:
            package.title = title

    actions = 0

    def walk(element: ET.Element, inside_archive: bool) -> None:
        nonlocal actions
        for child in element:
            tag = child.tag.lower()
            if tag == "archive":
                actions += 1
                package.requires_openiv = True
                target = _normalize_target(child.get("path", "")) or _normalize_target(_text(child)) or ""
                package.openiv_actions.append(f"Создание/изменение архива: {target or 'RPF'}")
                walk(child, True)
                continue
            if tag == "text":
                actions += 1
                package.requires_openiv = True
                target = _normalize_target(_text(child)) or ""
                package.openiv_actions.append(f"Текстовое изменение внутри RPF: {target or 'файл'}")
                continue
            if tag == "delete":
                actions += 1
                package.requires_openiv = True
                target = _normalize_target(_text(child)) or ""
                package.openiv_actions.append(f"Удаление файла внутри RPF: {target or 'файл'}")
                continue
            if tag not in {"add", "replace"}:
                walk(child, inside_archive)
                continue
            actions += 1
            target = _normalize_target(child.get("target") or _text(child) or "")
            source_value = (child.get("source") or "").strip()
            if not target:
                package.warnings.append(f"Пропущено действие {tag} без пути назначения.")
                continue
            if inside_archive or not _looks_loose(target):
                package.requires_openiv = True
                package.openiv_actions.append(f"{'Замена' if tag == 'replace' else 'Добавление'} через OpenIV: {target}")
                continue
            if not source_value:
                package.warnings.append(f"Пропущен файл без источника: {target}")
                continue
            source_path = (base / source_value).resolve()
            if not is_subpath(source_path, base) or not source_path.exists():
                package.warnings.append(f"В OIV отсутствует файл {source_value}.")
                continue
            files = _source_files(source_path)
            if not files:
                continue
            if source_path.is_dir() or target.endswith("/"):
                for file in files:
                    relative = file.relative_to(source_path).as_posix()
                    dest = target.rstrip("/") + "/" + relative
                    package.files.append(OivFile(file, dest, tag))
            else:
                package.files.append(OivFile(files[0], target, tag))

    content = document.find("content")
    if content is not None:
        walk(content, False)
    else:
        walk(document, False)
    if actions == 0:
        package.warnings.append("В assembly.xml не найдено инструкций по установке.")
    return package
