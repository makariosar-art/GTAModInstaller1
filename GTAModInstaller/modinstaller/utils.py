from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

ProgressCB = Callable[[Optional[int], str], None]


def noop_progress(percent: Optional[int], message: str) -> None:
    del percent, message


def human_size(num: float | int | None) -> str:
    if num is None:
        return "—"
    value = float(num)
    if value < 1024:
        return f"{int(value)} Б"
    for unit in ("КБ", "МБ", "ГБ", "ТБ"):
        value /= 1024.0
        if value < 1024 or unit == "ТБ":
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}".replace(".00", "").replace("0 " + unit, " " + unit)
    return "—"


def human_count(num: int) -> str:
    return f"{num:,}".replace(",", " ")


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    value = abs(int(count)) % 100
    if 11 <= value <= 14:
        return many
    value %= 10
    if value == 1:
        return one
    if 2 <= value <= 4:
        return few
    return many


def files_word(count: int) -> str:
    return plural_ru(count, "файл", "файла", "файлов")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def is_windows() -> bool:
    return os.name == "nt"


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_subpath(path: Path | str, parent: Path | str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_segment(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = _INVALID_CHARS.sub("_", name).strip().rstrip(" .")
    if not name:
        return "_"
    if name.lower() in _RESERVED_NAMES:
        name = "_" + name
    return name[:180]


def sanitize_dest_rel(path: str) -> str | None:
    value = path.replace("\\", "/").strip().lstrip("/")
    if not value:
        return None
    parts: list[str] = []
    for part in value.split("/"):
        part = part.strip()
        if not part or part in (".", ".."):
            return None
        if ":" in part:
            return None
        parts.append(sanitize_segment(part))
    return "/".join(parts)


def safe_slug(text: str, fallback: str = "mod", limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s.-]+", "", text).strip()
    text = re.sub(r"[\s_]+", "-", text).strip("-.").lower()
    return (text or fallback)[:limit].strip("-.") or fallback


def timestamp_slug() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def format_local_time(ts: float | None = None) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts or time.time()))


def format_relative_date(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def read_text_limited(path: Path, limit: int = 512_000) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return ""
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            return data.decode(encoding, errors="strict")
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def copy_file_progress(
    src: Path,
    dst: Path,
    on_bytes: Callable[[int], None] | None = None,
    chunk_size: int = 1024 * 1024,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = dst.with_name(dst.name + ".part")
    try:
        with open(src, "rb") as fsrc, open(temp, "wb") as fdst:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
        try:
            os.chmod(dst, stat.S_IWRITE)
        except OSError:
            pass
        os.replace(temp, dst)
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def make_writable(path: Path) -> None:
    try:
        if path.exists() and not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def open_folder(path: Path | str) -> None:
    path = Path(path)
    if not path.exists():
        path = path.parent
    try:
        os.startfile(str(path))
    except Exception:
        try:
            subprocess.Popen(["explorer", str(path)])
        except Exception:
            pass


def reveal_path(path: Path | str) -> None:
    path = Path(path)
    try:
        if path.is_dir():
            os.startfile(str(path))
        elif path.exists():
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            open_folder(path.parent)
    except Exception:
        pass


def open_url(url: str) -> None:
    try:
        os.startfile(url)
    except Exception:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass


def remove_tree(path: Path, ignore_errors: bool = True) -> None:
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=ignore_errors)
    except Exception:
        pass


def directory_size(path: Path, limit_files: int = 50_000) -> int:
    total = 0
    count = 0
    if not path.exists():
        return 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
            count += 1
            if count >= limit_files:
                return total
    return total


def clean_name_from_file(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"^[0-9a-f]{6,10}-", "", stem, flags=re.I)
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip(" -_.")
    return stem or name


def unique_destination(directory: Path, filename: str) -> Path:
    base = directory / filename
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{int(time.time())}{suffix}"


def hidden_or_junk(relative: Path) -> bool:
    parts = {part.lower() for part in relative.parts}
    if parts & {"__macosx", ".git", ".svn", ".vs", "__pycache__", ".idea"}:
        return True
    name = relative.name.lower()
    return name in {"thumbs.db", "desktop.ini", ".ds_store"} or name.startswith("._")


def parse_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0
