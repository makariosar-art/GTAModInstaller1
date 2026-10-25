from __future__ import annotations

import argparse
import os
import sys
import traceback


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GTA V Mod Installer")
    parser.add_argument("paths", nargs="*", help="Mod archives or folders to open")
    parser.add_argument("--game", help="Path to a GTA V installation (not saved)")
    parser.add_argument("--page", default="install", choices=["install", "online", "deps", "backups", "log"])
    parser.add_argument("--selftest", action="store_true", help="Run installer self-tests and exit")
    parser.add_argument("--selftest-file", help="Write self-test output to this file")
    return parser.parse_args(argv)


def _write_selftest_output(text: str, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    try:
        print(text)
    except Exception:
        pass


def _run_selftest(out_file: str | None) -> int:
    from modinstaller.selftest import run_selftest

    lines: list[str] = []
    try:
        ok = run_selftest(lambda line: lines.append(line))
    except Exception:
        lines.append("SELFTEST CRASHED")
        lines.append(traceback.format_exc())
        ok = False
    output = "\n".join(lines)
    _write_selftest_output(output, out_file)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.selftest:
        return _run_selftest(args.selftest_file)

    if os.name != "nt":
        print("This application targets Windows (GTA V).")
        return 2

    try:
        from PySide6.QtCore import QLockFile, QStandardPaths
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception as exc:  # pragma: no cover - missing runtime deps
        print(f"PySide6 is required to run the UI: {exc}")
        return 2

    from modinstaller.config import Config
    from modinstaller.ui.app import MainWindow
    from modinstaller.ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("GTA V Mod Installer")
    app.setOrganizationName("ModDeckTools")
    app.setApplicationVersion(__import__("modinstaller").__version__)
    apply_theme(app)

    lock_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation) or os.environ.get("TEMP", ".")
    lock = QLockFile(os.path.join(lock_dir, "gtav_mod_installer.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(None, "GTA V Mod Installer", "Приложение уже запущено.")
        return 0

    config = Config.load()
    window = MainWindow(config)
    if args.game:
        window.use_game_override(args.game)
    window.show()
    if args.paths:
        window.add_paths_later([os.path.abspath(p) for p in args.paths])
    if args.page != "install":
        window.show_page_by_key(args.page, silent=True)
    code = app.exec()
    window.shutdown()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
