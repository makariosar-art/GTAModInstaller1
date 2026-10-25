"""Package dist/GTAModInstaller into a single self-extracting GTAModInstaller.exe.

The payload is compressed with 7-Zip (LZMA2 + BCJ2) and glued to the tiny
7zS2.sfx module, which extracts to %TEMP% and starts the app with the original
command line (drag-and-drop / --selftest still work). Requires 7-Zip installed.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "dist" / "GTAModInstaller"
APP_EXE = APP_DIR / "GTAModInstaller.exe"
STUB = ROOT / "tools" / "7zS2.sfx"
ICON = ROOT / "assets" / "icon.ico"
VERSION_FILE = ROOT / "version_info.txt"
OUTPUT = ROOT / "dist" / "GTAModInstaller.exe"

MAX_BYTES = 23_000_000

RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16
RT_MANIFEST = 24
LANG_EN_US = 1033

# 7zS2.sfx is a 32-bit executable without a manifest. Windows' installer
# detection then auto-elevates it because the file name contains "Installer",
# so the stub carries an explicit asInvoker manifest.
MANIFEST = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
</assembly>
"""

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
_kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
_kernel32.UpdateResourceW.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID,
    wintypes.WORD, wintypes.LPVOID, wintypes.DWORD,
]
_kernel32.UpdateResourceW.restype = wintypes.BOOL
_kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
_kernel32.EndUpdateResourceW.restype = wintypes.BOOL


def _resource_id(value: int) -> ctypes.c_void_p:
    return ctypes.c_void_p(value)


def _update(handle, res_type: int, res_id: int, data: bytes, language: int = LANG_EN_US) -> None:
    buffer = ctypes.create_string_buffer(data, len(data))
    ok = _kernel32.UpdateResourceW(
        handle, _resource_id(res_type), _resource_id(res_id), language, buffer, len(data)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _apply_icon(handle, ico_path: Path) -> None:
    ico = ico_path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", ico, 0)
    if reserved != 0 or ico_type != 1 or count == 0:
        raise ValueError(f"{ico_path.name}: not a valid .ico file")

    entries = []
    for index in range(count):
        width, height, colors, _, planes, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", ico, 6 + 16 * index
        )
        entries.append((width, height, colors, planes, bpp, size, offset))

    group = struct.pack("<HHH", 0, 1, len(entries))
    for icon_id, (width, height, colors, planes, bpp, size, offset) in enumerate(entries, 1):
        _update(handle, RT_ICON, icon_id, ico[offset:offset + size])
        group += struct.pack("<BBBBHHIH", width, height, colors, 0, planes, bpp, size, icon_id)
    _update(handle, RT_GROUP_ICON, 1, group)


def _apply_version(handle) -> None:
    try:
        from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
    except ImportError:
        print("warning: PyInstaller not found, SFX version info left unchanged", file=sys.stderr)
        return
    info = load_version_info_from_text_file(str(VERSION_FILE))
    _update(handle, RT_VERSION, 1, info.toRaw())


def patch_resources(stub: Path) -> None:
    handle = _kernel32.BeginUpdateResourceW(str(stub), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _apply_icon(handle, ICON)
        _apply_version(handle)
        _update(handle, RT_MANIFEST, 1, MANIFEST)
    except Exception:
        _kernel32.EndUpdateResourceW(handle, True)
        raise
    if not _kernel32.EndUpdateResourceW(handle, False):
        raise ctypes.WinError(ctypes.get_last_error())


def find_7zip() -> str:
    configured = os.environ.get("SEVENZIP")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("7z")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidate = Path(base) / "7-Zip" / "7z.exe"
        if candidate.is_file():
            return str(candidate)
    raise SystemExit("7-Zip not found: install it or set the SEVENZIP environment variable")


def build_archive(sevenzip: str, destination: Path) -> None:
    result = subprocess.run(
        [sevenzip, "a", "-t7z", "-mx=9", "-mmt=on", str(destination), "*", "-r"],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"7-Zip failed:\n{result.stdout}\n{result.stderr}")


def main() -> int:
    if not APP_EXE.is_file():
        raise SystemExit(f"{APP_EXE} not found - run PyInstaller first")
    if not STUB.is_file():
        raise SystemExit(f"{STUB} not found")

    sevenzip = find_7zip()
    with tempfile.TemporaryDirectory(prefix="gmi_sfx_") as temporary:
        archive = Path(temporary) / "payload.7z"
        stub = Path(temporary) / "stub.exe"
        print("Compressing payload...")
        build_archive(sevenzip, archive)
        shutil.copy2(STUB, stub)
        try:
            patch_resources(stub)
        except OSError as exc:
            print(f"warning: could not update SFX icon/version: {exc}", file=sys.stderr)

        if OUTPUT.exists():
            OUTPUT.unlink()
        with open(OUTPUT, "wb") as output, open(stub, "rb") as stub_file, open(archive, "rb") as payload:
            shutil.copyfileobj(stub_file, output)
            shutil.copyfileobj(payload, output)

    size = OUTPUT.stat().st_size
    print(f"Built {OUTPUT} - {size / 1e6:.2f} MB ({size / 2**20:.2f} MiB)")
    if size > MAX_BYTES:
        print(f"warning: exceeds the {MAX_BYTES / 1e6:.0f} MB target", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
