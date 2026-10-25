from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import string
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .utils import ProgressCB, human_size, is_windows, noop_progress

ROCKSTAR_KEYS = (
    (0x80000001, r"Software\Rockstar Games\Grand Theft Auto V"),  # HKCU
    (0x80000002, r"Software\Rockstar Games\Grand Theft Auto V"),  # HKLM
    (0x80000002, r"Software\WOW6432Node\Rockstar Games\Grand Theft Auto V"),
    (0x80000001, r"Software\Rockstar Games\Grand Theft Auto V Enhanced"),
    (0x80000002, r"Software\WOW6432Node\Rockstar Games\Grand Theft Auto V Enhanced"),
)

GAME_FOLDER_NAMES = {
    "grand theft auto v",
    "grand theft auto 5",
    "gta v",
    "gtav",
    "gta 5",
    "grand theft auto v legacy",
    "grand theft auto v enhanced",
    "gtav enhanced",
}

EXE_NAMES = ("GTA5.exe", "GTA5_Enhanced.exe")


@dataclass
class GameInfo:
    path: Path
    exe: Path
    edition: str
    version: str = ""
    writable: bool = False
    has_mods: bool = False
    has_openiv: bool = False
    source: str = ""

    @property
    def label(self) -> str:
        return f"GTA V {self.edition}" if self.edition else "GTA V"

    @property
    def has_asi_loader(self) -> bool:
        return (self.path / "dinput8.dll").exists()

    def describe(self) -> str:
        bits = [str(self.path)]
        if self.version:
            bits.append(f"v{self.version}")
        return " · ".join(bits)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def file_version(path: Path) -> str:
    if not is_windows():
        return ""
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ""

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return ""
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        return version if version.strip("0.") else ""
    except Exception:
        return ""


def _test_writable(path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(prefix=".modinstaller_", dir=path, delete=True):
            return True
    except OSError:
        return False


def build_game_info(path: Path | str, source: str = "") -> Optional[GameInfo]:
    directory = Path(path)
    if directory.is_file() and directory.name.lower() in {name.lower() for name in EXE_NAMES}:
        directory = directory.parent
    try:
        directory = directory.resolve()
    except OSError:
        return None
    for exe_name in EXE_NAMES:
        exe = directory / exe_name
        if exe.is_file():
            edition = "Enhanced" if "enhanced" in exe_name.lower() else "Legacy"
            return GameInfo(
                path=directory,
                exe=exe,
                edition=edition,
                version=file_version(exe),
                writable=_test_writable(directory),
                has_mods=(directory / "mods").is_dir(),
                has_openiv=(directory / "OpenIV.asi").is_file(),
                source=source,
            )
    return None


def _candidate_paths() -> Iterable[tuple[Path, str]]:
    seen: set[str] = set()

    def add(path: Path | str | None, source: str) -> Iterable[tuple[Path, str]]:
        if not path:
            return
        try:
            candidate = Path(str(path).strip().strip('"'))
        except (OSError, ValueError):
            return
        if not str(candidate):
            return
        key = _norm(candidate)
        if key in seen:
            return
        seen.add(key)
        yield candidate, source

    if is_windows():
        import winreg

        for hive, subkey in ROCKSTAR_KEYS:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    index = 0
                    while True:
                        try:
                            name, value, _kind = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        index += 1
                        if isinstance(value, str) and ":" in value and name.lower() not in {"language"}:
                            for item in add(value, "Rockstar"):
                                yield item
            except OSError:
                pass

        steam_roots: list[str] = []
        for hive, subkey, value_name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
                if value:
                    steam_roots.append(str(value).replace("/", os.sep))
            except OSError:
                pass
        for steam_root in steam_roots:
            yield Path(steam_root) / "steamapps" / "common" / "Grand Theft Auto V", "Steam"
            vdf = Path(steam_root) / "steamapps" / "libraryfolders.vdf"
            try:
                text = vdf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                library = match.group(1).replace("\\\\", "\\")
                yield Path(library) / "steamapps" / "common" / "Grand Theft Auto V", "Steam"

        epic_manifests = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        if epic_manifests.is_dir():
            for manifest in epic_manifests.glob("*.item"):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, ValueError):
                    continue
                display = str(data.get("DisplayName", ""))
                if "grand theft auto" in display.lower() and " v" in display.lower():
                    location = data.get("InstallLocation")
                    if location:
                        yield Path(location), "Epic Games"

    drives = [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    suffixes = (
        r"Program Files\Rockstar Games\Grand Theft Auto V",
        r"Program Files\Epic Games\GTAV",
        r"Program Files\Epic Games\GTA V",
        r"Rockstar Games\Grand Theft Auto V",
        r"Games\Grand Theft Auto V",
        r"SteamLibrary\steamapps\common\Grand Theft Auto V",
        r"Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V",
    )
    for drive in drives:
        for suffix in suffixes:
            yield Path(drive) / suffix, "Поиск"


def detect_games(deep: bool = False, progress: ProgressCB = noop_progress) -> list[GameInfo]:
    games: list[GameInfo] = []
    seen: set[str] = set()

    def try_add(path: Path, source: str) -> None:
        info = build_game_info(path, source)
        if not info:
            return
        key = _norm(info.path)
        if key in seen:
            return
        seen.add(key)
        games.append(info)

    for path, source in _candidate_paths():
        try_add(path, source)
    if deep:
        for path, source in deep_scan(progress):
            try_add(path, source)
    games.sort(key=lambda game: (game.edition != "Legacy", game.source != "Steam"))
    return games


def deep_scan(progress: ProgressCB = noop_progress, time_limit: float = 18.0) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []
    if not is_windows():
        return results
    skip_names = {
        "windows",
        "windows.old",
        "programdata",
        "$recycle.bin",
        "system volume information",
        "appdata",
        "node_modules",
        "recovery",
        "perflogs",
        "documents and settings",
        "msocache",
        "config.msi",
        "temp",
        "tmp",
        "$windows.~bt",
        "$windows.~ws",
        "windowsapps",
        "steamapps",  # handled explicitly below
        "common",  # handled explicitly below
    }
    blocked_fragments = ("\\windows\\", "\\$recycle", "\\system volume", "\\windowsapps\\", "\\appdata\\")
    start = time.monotonic()
    visited = 0
    drives = [Path(f"{letter}:\\") for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    progress(None, "Глубокий поиск установленной игры…")
    for drive in drives:
        stack: list[tuple[Path, int]] = [(drive, 0)]
        while stack:
            current, depth = stack.pop()
            if time.monotonic() - start > time_limit:
                return results
            visited += 1
            if visited % 350 == 0:
                progress(None, f"Сканирование дисков… проверено папок: {visited}")
            try:
                with os.scandir(current) as entries:
                    children = list(entries)
            except OSError:
                continue
            for entry in children:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                name = entry.name.lower()
                path = Path(entry.path)
                low = str(path).lower()
                if name in GAME_FOLDER_NAMES:
                    results.append((path, "Глубокий поиск"))
                    continue
                if depth < 3 and name not in skip_names and not any(fragment in low + "\\" for fragment in blocked_fragments):
                    stack.append((path, depth + 1))
    return results


def is_game_running() -> tuple[bool, list[str]]:
    if not is_windows():
        return False, []
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=12,
            creationflags=0x08000000,
        )
        text = result.stdout.decode("cp866", errors="ignore") + result.stdout.decode("utf-8", errors="ignore")
        names: list[str] = []
        for exe in EXE_NAMES:
            if exe.lower() in text.lower():
                names.append(exe)
        return bool(names), names
    except Exception:
        return False, []


def disk_free(path: Path | str) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return 0


def editable_game_warning(game: GameInfo) -> str:
    if not game.writable:
        return (
            "Нет прав на запись в папку игры. Закройте игру и перезапустите установщик "
            "от имени администратора или установите игру вне Program Files."
        )
    return ""


def find_gta_from_any(path: Path | str) -> Optional[GameInfo]:
    return build_game_info(path)
