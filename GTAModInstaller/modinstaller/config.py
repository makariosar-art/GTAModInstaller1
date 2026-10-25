from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "GTAModInstaller"
    path.mkdir(parents=True, exist_ok=True)
    return path


APP_DIR = app_data_dir()
CACHE_DIR = APP_DIR / "cache"
STAGING_DIR = CACHE_DIR / "staging"
DOWNLOADS_DIR = APP_DIR / "downloads"
BACKUPS_DIR = APP_DIR / "backups"
LOGS_DIR = APP_DIR / "logs"
IMAGES_DIR = CACHE_DIR / "images"
CONFIG_PATH = APP_DIR / "config.json"

for _directory in (CACHE_DIR, STAGING_DIR, DOWNLOADS_DIR, BACKUPS_DIR, LOGS_DIR, IMAGES_DIR):
    try:
        _directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def purge_staging() -> None:
    try:
        for child in STAGING_DIR.iterdir():
            if child.is_dir():
                try:
                    import shutil

                    shutil.rmtree(child, ignore_errors=True)
                except OSError:
                    pass
    except OSError:
        pass


def purge_cached_images() -> None:
    try:
        for child in IMAGES_DIR.iterdir():
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
    except OSError:
        pass


@dataclass
class Config:
    game_path: str = ""
    auto_install_deps: bool = True
    make_backups: bool = True
    check_game_running: bool = True
    first_run: bool = True
    window_geometry: str = ""
    last_browse_dir: str = ""

    @classmethod
    def load(cls) -> "Config":
        data: dict = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
        known = {field.name for field in fields(cls)}
        clean = {key: value for key, value in data.items() if key in known}
        config = cls(**clean)
        return config

    def save(self) -> None:
        data = asdict(self)
        temp = CONFIG_PATH.with_suffix(".tmp")
        try:
            with open(temp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(temp, CONFIG_PATH)
        except OSError:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
