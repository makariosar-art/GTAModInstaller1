from __future__ import annotations

import functools
import gzip
import bz2
import lzma
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .utils import ProgressCB, hidden_or_junk, noop_progress

ARCHIVE_EXTS = {
    ".zip": "zip",
    ".oiv": "oiv",
    ".rar": "rar",
    ".7z": "7z",
    ".tar": "tar",
    ".gz": "tar",
    ".tgz": "tar",
    ".bz2": "tar",
    ".tbz2": "tar",
    ".xz": "tar",
    ".txz": "tar",
    ".lzma": "lzma",
}

PROTECTED_TOP = {"scripts", "mods", "lml", "update", "x64", "x64a", "x64b", "x64c", "common", "platform", "dlcpacks"}
GAME_WRAPPERS = {
    "grand theft auto v",
    "grand theft auto 5",
    "gta v",
    "gtav",
    "gta 5",
    "grand theft auto v legacy",
    "grand theft auto v enhanced",
}

DOC_EXTENSIONS = {".txt", ".md", ".nfo", ".pdf", ".rtf", ".doc", ".docx", ".url", ".lnk", ".jpg", ".jpeg", ".png", ".gif", ".webp"}


class ArchiveError(Exception):
    pass


class UnsupportedArchiveError(ArchiveError):
    pass


class PasswordProtectedError(ArchiveError):
    pass


def archive_kind(path: Path | str) -> str | None:
    name = Path(path).name.lower()
    suffix = Path(name).suffix.lower()
    if suffix == ".oiv":
        return "oiv"
    if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return "tar"
    return ARCHIVE_EXTS.get(suffix)


def is_archive(path: Path | str) -> bool:
    return archive_kind(path) is not None


def sniff_archive(path: Path | str) -> str | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if not head:
        return None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"\x1f\x8b"):
        return "tar"
    if head.startswith(b"BZh"):
        return "tar"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "tar"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    return None


@functools.lru_cache(maxsize=1)
def find_7zip() -> str | None:
    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.extend(
                [
                    Path(base) / "7-Zip" / "7z.exe",
                    Path(base) / "Programs" / "7-Zip" / "7z.exe",
                    Path(base) / "NanaZip" / "7z.exe",
                ]
            )
    which = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    if which:
        candidates.insert(0, Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@functools.lru_cache(maxsize=1)
def find_unrar() -> str | None:
    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "WinRAR" / "UnRAR.exe")
    which = shutil.which("unrar")
    if which:
        candidates.insert(0, Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def content_root(root: Path, aggressive: bool) -> Path:
    current = root
    for _ in range(5):
        try:
            entries = [entry for entry in current.iterdir() if not hidden_or_junk(entry.relative_to(root))]
        except OSError:
            break
        files = [entry for entry in entries if entry.is_file()]
        directories = [entry for entry in entries if entry.is_dir()]
        meaningful_files = [entry for entry in files if entry.suffix.lower() not in DOC_EXTENSIONS]
        meaningful_dirs = [entry for entry in directories if entry.name.lower() not in {"__macosx"}]
        if len(meaningful_dirs) != 1:
            break
        candidate = meaningful_dirs[0]
        is_game_wrapper = candidate.name.strip().lower() in GAME_WRAPPERS
        if candidate.name.strip().lower() in PROTECTED_TOP:
            break
        if aggressive and not meaningful_files:
            current = candidate
            continue
        if is_game_wrapper and not meaningful_files:
            current = candidate
            continue
        break
    return current


def _fs_parts(name: str) -> list[str] | None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return None
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts:
        return None
    if any(part == ".." for part in parts):
        return None
    if ":" in parts[0]:
        return None
    return parts


def _target_for(destination: Path, parts: list[str]) -> Path:
    target = destination.joinpath(*parts)
    resolved_destination = destination.resolve()
    try:
        target.resolve().relative_to(resolved_destination)
    except (ValueError, OSError) as exc:
        raise ArchiveError("Архив содержит недопустимые пути (path traversal).") from exc
    return target


def _extract_zip(path: Path, destination: Path, progress: ProgressCB) -> None:
    try:
        archive = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"Повреждён ZIP-архив: {path.name}") from exc
    with archive:
        infos = [info for info in archive.infolist() if not hidden_or_junk(Path(info.filename.replace("\\", "/")))]
        if any(info.flag_bits & 0x1 for info in infos):
            raise PasswordProtectedError("Архив защищён паролем — распакуйте его вручную.")
        total = max(1, sum(info.file_size for info in infos))
        done = 0
        for info in infos:
            parts = _fs_parts(info.filename)
            if not parts:
                continue
            target = _target_for(destination, parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info, "r") as source, open(target, "wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        done += len(chunk)
                        progress(min(99, int(done / total * 100)), f"Распаковка: {info.filename}")
            except RuntimeError as exc:
                if "password" in str(exc).lower():
                    raise PasswordProtectedError("Архив защищён паролем — распакуйте его вручную.") from exc
                raise ArchiveError(f"Не удалось распаковать {info.filename}: {exc}") from exc
        progress(100, "Архив распакован")


def _extract_tar(path: Path, destination: Path, progress: ProgressCB) -> None:
    try:
        archive = tarfile.open(path, "r:*")
    except tarfile.TarError as exc:
        raise ArchiveError(f"Повреждён TAR-архив: {path.name}") from exc
    with archive:
        try:
            members = archive.getmembers()
        except (tarfile.TarError, OSError) as exc:
            raise ArchiveError(f"Не удалось прочитать архив: {exc}") from exc
        total = max(1, sum(member.size for member in members if member.isfile()))
        done = 0
        for member in members:
            relative = Path(member.name.replace("\\", "/"))
            if hidden_or_junk(relative):
                continue
            parts = _fs_parts(member.name)
            if not parts:
                continue
            target = _target_for(destination, parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    progress(min(99, int(done / total * 100)), f"Распаковка: {member.name}")
        progress(100, "Архив распакован")


def _cli_extract(tool: list[str], path: Path, destination: Path, progress: ProgressCB, label: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    command = [*tool, "x", "-y", f"-o{destination}", str(path)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=900,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArchiveError(f"Не удалось запустить распаковщик {label}: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("cp866", errors="ignore") + (result.stderr or b"").decode("utf-8", errors="ignore")
        combined = stderr.lower()
        if "password" in combined or "парол" in combined:
            raise PasswordProtectedError("Архив защищён паролем — распакуйте его вручную.")
        if "can not open" in combined or "cannot open" in combined:
            raise ArchiveError(f"Не удалось открыть архив {path.name}. Возможно, он повреждён.")
        raise ArchiveError(f"{label} не смог распаковать {path.name}.\n{stderr.strip()[:500]}")
    progress(100, "Архив распакован")


def _extract_7z(path: Path, destination: Path, progress: ProgressCB) -> None:
    tool = find_7zip()
    if tool:
        progress(None, "Распаковка 7-Zip…")
        _cli_extract([tool], path, destination, progress, "7-Zip")
        return
    try:
        import py7zr
    except ImportError as exc:
        raise UnsupportedArchiveError(
            "Для распаковки .7z установите 7-Zip или модуль py7zr."
        ) from exc
    progress(None, "Распаковка 7z…")
    try:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            archive.extractall(path=str(destination))
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise PasswordProtectedError("Архив защищён паролем — распакуйте его вручную.") from exc
        raise ArchiveError(f"Не удалось распаковать 7z-архив: {exc}") from exc
    progress(100, "Архив распакован")


def _extract_rar(path: Path, destination: Path, progress: ProgressCB) -> None:
    tool = find_7zip()
    if tool:
        progress(None, "Распаковка RAR через 7-Zip…")
        _cli_extract([tool], path, destination, progress, "7-Zip")
        return
    unrar = find_unrar()
    if unrar:
        progress(None, "Распаковка RAR…")
        destination.mkdir(parents=True, exist_ok=True)
        command = [unrar, "x", "-y", "-o+", str(path), str(destination) + os.sep]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=900, creationflags=0x08000000 if os.name == "nt" else 0
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArchiveError(f"Не удалось запустить UnRAR: {exc}") from exc
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("cp866", errors="ignore") or (result.stdout or b"").decode("cp866", errors="ignore")
            if "password" in stderr.lower() or "парол" in stderr.lower():
                raise PasswordProtectedError("Архив защищён паролем — распакуйте его вручную.")
            raise ArchiveError(f"UnRAR не смог распаковать архив.\n{stderr[:500]}")
        progress(100, "Архив распакован")
        return
    raise UnsupportedArchiveError(
        "Для RAR-архивов нужен 7-Zip или WinRAR. Установите 7-Zip и повторите попытку."
    )


def _extract_single_compressed(path: Path, destination: Path, progress: ProgressCB) -> None:
    suffix = path.suffix.lower()
    destination.mkdir(parents=True, exist_ok=True)
    output_name = path.name[: -len(suffix)] or "extracted"
    target = destination / output_name
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open, ".lzma": lzma.open}.get(suffix)
    if not opener:
        raise UnsupportedArchiveError(f"Неподдерживаемый формат: {path.suffix}")
    progress(None, "Распаковка…")
    try:
        with opener(path, "rb") as source, open(target, "wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
    except OSError as exc:
        raise ArchiveError(f"Не удалось распаковать файл: {exc}") from exc
    progress(100, "Архив распакован")


def extract_archive(path: Path, destination: Path, progress: ProgressCB = noop_progress) -> None:
    path = Path(path)
    destination = Path(destination)
    if not path.is_file():
        raise ArchiveError(f"Файл не найден: {path}")
    destination.mkdir(parents=True, exist_ok=True)
    kind = archive_kind(path) or sniff_archive(path)
    progress(0, f"Распаковка: {path.name}")
    if kind in ("zip", "oiv"):
        _extract_zip(path, destination, progress)
    elif kind == "tar":
        name = path.name.lower()
        if name.endswith((".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz")):
            _extract_tar(path, destination, progress)
        else:
            _extract_single_compressed(path, destination, progress)
    elif kind == "7z":
        _extract_7z(path, destination, progress)
    elif kind == "rar":
        _extract_rar(path, destination, progress)
    else:
        if path.suffix.lower() in (".gz", ".bz2", ".xz", ".lzma"):
            _extract_single_compressed(path, destination, progress)
        else:
            raise UnsupportedArchiveError(f"Неподдерживаемый формат архива: {path.name}")
