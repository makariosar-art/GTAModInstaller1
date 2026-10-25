from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path


def run_selftest(emit) -> bool:
    started = time.monotonic()
    checks = 0
    failures = 0

    def line(text: str = "") -> None:
        emit(text)

    def check(name: str, condition: bool, detail: str = "") -> bool:
        nonlocal checks, failures
        checks += 1
        if condition:
            line(f"  [OK]   {name}")
            return True
        failures += 1
        line(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
        return False

    def section(title: str, callback) -> None:
        nonlocal failures
        line()
        line(f"— {title} —")
        try:
            callback()
        except Exception:
            failures += 1
            line("  [FAIL] Раздел прерван исключением:")
            for row in traceback.format_exc().splitlines():
                line(f"         {row}")

    line("=== GTA V Mod Installer · самопроверка ===")
    state = "exe" if getattr(sys, "frozen", False) else "исходники"
    line(f"Python {sys.version.split()[0]} · {sys.platform} · запуск: {state}")

    workspace = Path(tempfile.mkdtemp(prefix="gtamod-selftest-"))
    patched: list[tuple[object, str, object]] = []
    try:
        from bs4 import BeautifulSoup

        from modinstaller import installer, staging
        from modinstaller.analyzer import Analyzer, dependencies_missing, plan_summary
        from modinstaller.archives import extract_archive, find_7zip
        from modinstaller.config import resource_path
        from modinstaller.deps_defs import DEP_BY_KEY
        from modinstaller.game import build_game_info
        from modinstaller.installer import (
            InstallError,
            install_plans,
            list_backups,
            list_installed_mods,
            remove_installed_mod,
            rollback_manifest,
        )
        from modinstaller.oiv import parse_oiv
        from modinstaller.site import (
            SiteError,
            _card_from_element,
            _check_magic,
            _safe_remote_url,
            load_catalog,
        )
        from modinstaller.staging import prepare_source
        from modinstaller.utils import files_word, human_size, plural_ru, sanitize_dest_rel
        from modinstaller.workflow import pending_auto_dependencies

        for module, name, value in (
            (installer, "BACKUPS_DIR", workspace / "backups"),
            (staging, "STAGING_DIR", workspace / "staging"),
        ):
            patched.append((module, name, getattr(module, name)))
            setattr(module, name, value)
        (workspace / "backups").mkdir(parents=True, exist_ok=True)
        (workspace / "staging").mkdir(parents=True, exist_ok=True)

        game_dir = workspace / "game" / "Grand Theft Auto V"
        game_dir.mkdir(parents=True)
        (game_dir / "GTA5.exe").write_bytes(b"MZ\x90\x00selftest")
        (game_dir / "mods").mkdir()

        def write(path: Path, data: bytes | str) -> Path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, str):
                path.write_text(data, encoding="utf-8")
            else:
                path.write_bytes(data)
            return path

        def zip_tree(archive_path: Path, root: Path, prefix: str = "") -> Path:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(root.rglob("*")):
                    if not file.is_file():
                        continue
                    rel = file.relative_to(root).as_posix()
                    archive.write(file, f"{prefix}/{rel}" if prefix else rel)
            return archive_path

        def make_mod_tree(base: Path) -> Path:
            root = base / "TestMod"
            write(root / "TestMod.asi", b"ASI-NEW-" * 64)
            write(root / "TestMod.ini", "[settings]\nEnabled=1\n")
            write(root / "scripts" / "TestScript.dll", b"DLL-NEW-" * 96)
            write(root / "README.txt", "Installation\nNeeds Script Hook V and Script Hook V .NET.\n")
            return root

        def test_utils() -> None:
            check("Обычный путь сохраняется", sanitize_dest_rel("scripts/Test.dll") == "scripts/Test.dll")
            check("Обход каталога отклоняется", sanitize_dest_rel("../evil.dll") is None)
            check("Windows-обход отклоняется", sanitize_dest_rel("..\\evil.dll") is None)
            check("Путь с диском отклоняется", sanitize_dest_rel("C:/Windows/evil.dll") is None)
            check("Пустое имя отклоняется", sanitize_dest_rel("   ") is None)
            check("Склонение: 1 файл", files_word(1) == "файл")
            check("Склонение: 3 файла", files_word(3) == "файла")
            check("Склонение: 11 файлов", files_word(11) == "файлов")
            check("Склонение: 21 мод", plural_ru(21, "мод", "мода", "модов") == "мод")
            check("Размер 1024 Б", human_size(1024) == "1 КБ", human_size(1024))

        def test_game() -> None:
            info = build_game_info(game_dir)
            check(
                "GTA V (Legacy) распознана",
                info is not None and info.edition == "Legacy",
                info.edition if info else "не найдена",
            )
            check("Путь игры указывает на папку", info is not None and info.path == game_dir.resolve())
            check("Определены права на запись", bool(info and info.writable))
            info_exe = build_game_info(game_dir / "GTA5.exe")
            check("Путь к GTA5.exe принимается", info_exe is not None and info_exe.path == game_dir.resolve())
            enhanced = workspace / "game_enhanced"
            write(enhanced / "GTA5_Enhanced.exe", b"MZ")
            info_enhanced = build_game_info(enhanced)
            check(
                "Enhanced-издание распознано",
                info_enhanced is not None and info_enhanced.edition == "Enhanced",
            )
            empty = workspace / "not_game"
            empty.mkdir()
            check("Папка без GTA5.exe отклонена", build_game_info(empty) is None)

        def test_archives() -> None:
            base = workspace / "archive_case"
            root = make_mod_tree(base)
            archive = zip_tree(base / "TestMod.zip", root, "TestMod")
            out = base / "out"
            extract_archive(archive, out)
            check(
                "ZIP распакован с сохранением папок",
                (out / "TestMod" / "TestMod.asi").is_file()
                and (out / "TestMod" / "scripts" / "TestScript.dll").is_file(),
            )
            prepared = prepare_source(archive)
            check("Архив подготовлен как мод", prepared.is_archive and bool(prepared.roots), prepared.title)
            check("Имя мода взято из архива", prepared.title == "TestMod", prepared.title)
            check(
                "Содержимое архива доступно",
                any((root / "TestMod" / "TestMod.asi").is_file() for root in prepared.roots),
            )
            check(
                "Папка мода внутри архива сохранена",
                any((root / "TestMod").is_dir() for root in prepared.roots),
            )

            mixed = base / "mixed.zip"
            with zipfile.ZipFile(mixed, "w") as zf:
                zf.writestr("safe/ok.txt", "ok")
                zf.writestr("../outside.txt", "bad")
                zf.writestr("/absolute.txt", "bad")
            mixed_out = base / "mixed_out"
            extract_archive(mixed, mixed_out)
            check("Безопасные файлы распакованы", (mixed_out / "safe" / "ok.txt").is_file())
            escaped = [
                base / "outside.txt",
                workspace / "outside.txt",
                base / "absolute.txt",
                mixed_out / "absolute.txt",
            ]
            check("Опасные пути не созданы", not any(path.exists() for path in escaped))

            nested_base = workspace / "nested_case"
            wrapper = nested_base / "Wrapper"
            write(wrapper / "Main.asi", b"main")
            inner = nested_base / "inner_src"
            write(inner / "scripts" / "Extra.dll", b"extra")
            zip_tree(wrapper / "extra.zip", inner)
            nested = prepare_source(wrapper)
            check(
                "Вложенный архив распакован",
                any((root / "scripts" / "Extra.dll").is_file() for root in nested.roots),
            )
            check(
                "Вложенный архив помечен использованным",
                any(path.name == "extra.zip" for path in nested.consumed),
            )
            tool = find_7zip()
            line(f"  [INFO] RAR/7Z: {tool or '7-Zip не найден, нужен для этих форматов'}")

        def test_analyze() -> None:
            base = workspace / "analyze_case"
            root = make_mod_tree(base)
            source = prepare_source(root)
            plan = Analyzer(str(game_dir)).analyze(source)
            items = {item.dest_rel.lower(): item for item in plan.items}
            check("ASI → корень игры", "testmod.asi" in items and items["testmod.asi"].kind == "asi")
            check(
                "SHVDN DLL → scripts",
                "scripts/testscript.dll" in items and items["scripts/testscript.dll"].kind == "script",
            )
            check("INI → корень игры", "testmod.ini" in items)
            check(
                "README не попадает в план",
                not any(item.dest_rel.lower().endswith("readme.txt") for item in plan.items),
            )
            check("ASI требует Script Hook V", "shv" in plan.deps)
            check("SHVDN-скрипт требует .NET", "shvdn" in plan.deps)
            missing = dependencies_missing([plan])
            check("Недостающие зависимости найдены", "shv" in missing and "shvdn" in missing)
            check("README найден и прочитан", any(doc.name.lower() == "readme.txt" for doc in plan.readmes))
            files, size, replaced, manual = plan_summary([plan])
            check("В плане 3 файла", (files, manual) == (3, 0), f"файлов={files}, вручную={manual}")
            check("Размер плана больше нуля", size > 0)
            check("Совпадений с игрой нет", files == 3 and replaced == 0)

            archive = zip_tree(base / "Pack.zip", root)
            archive_source = prepare_source(archive)
            archive_plan = Analyzer(str(game_dir)).analyze(archive_source)
            check(
                "Архив даёт тот же план",
                len(archive_plan.enabled_items) == 3,
                f"файлов={len(archive_plan.enabled_items)}",
            )

        def test_mod_folders() -> None:
            base = workspace / "mod_folders"
            mod = base / "SinglePlayerApartment"
            write(mod / "SinglePlayerApartment.dll", b"MZ-SPA-DLL")
            write(mod / "settings.cfg", "SpawnPoint=1\n")
            write(mod / "data" / "config.json", '{"enabled": true}')
            source = prepare_source(mod)
            plan = Analyzer(str(game_dir)).analyze(source)
            dests = {item.dest_rel.lower() for item in plan.items if item.enabled}
            check(
                "SHVDN DLL сохраняет папку мода",
                "scripts/singleplayerapartment/singleplayerapartment.dll" in dests,
                ", ".join(sorted(dests)),
            )
            check(
                "settings.cfg установлен в папку мода",
                "scripts/singleplayerapartment/settings.cfg" in dests,
                ", ".join(sorted(dests)),
            )
            check(
                "Данные мода сохраняют структуру",
                "scripts/singleplayerapartment/data/config.json" in dests,
                ", ".join(sorted(dests)),
            )
            check("SHVDN определён как зависимость", "shvdn" in plan.deps)

            archive = zip_tree(base / "SinglePlayerApartment.zip", mod, "SinglePlayerApartment")
            archive_plan = Analyzer(str(game_dir)).analyze(prepare_source(archive))
            archive_dests = {item.dest_rel.lower() for item in archive_plan.items if item.enabled}
            check(
                "ZIP сохраняет папку мода для DLL",
                "scripts/singleplayerapartment/singleplayerapartment.dll" in archive_dests,
                ", ".join(sorted(archive_dests)),
            )
            check(
                "ZIP: settings.cfg в папке мода",
                "scripts/singleplayerapartment/settings.cfg" in archive_dests,
                ", ".join(sorted(archive_dests)),
            )

            cfg_only = base / "CfgOnly" / "SinglePlayerApartment"
            write(cfg_only / "settings.cfg", "x=1")
            cfg_plan = Analyzer(str(game_dir)).analyze(prepare_source(cfg_only))
            cfg_dests = {item.dest_rel.lower() for item in cfg_plan.items}
            check(
                "Папка только с конфигом → scripts/<папка>",
                "scripts/singleplayerapartment/settings.cfg" in cfg_dests,
                ", ".join(sorted(cfg_dests)),
            )

            ready = base / "ReadyMod"
            write(ready / "scripts" / "SinglePlayerApartment" / "SinglePlayerApartment.dll", b"MZ")
            write(ready / "scripts" / "SinglePlayerApartment" / "settings.cfg", "x=1")
            ready_plan = Analyzer(str(game_dir)).analyze(prepare_source(ready))
            ready_dests = {item.dest_rel.lower() for item in ready_plan.items}
            check(
                "Готовая структура scripts не меняется",
                "scripts/singleplayerapartment/settings.cfg" in ready_dests
                and "scripts/singleplayerapartment/singleplayerapartment.dll" in ready_dests,
                ", ".join(sorted(ready_dests)),
            )

            asi = base / "AsiMod"
            write(asi / "AsiMod.asi", b"ASI")
            write(asi / "AsiMod.ini", "[settings]\n")
            asi_plan = Analyzer(str(game_dir)).analyze(prepare_source(asi))
            asi_dests = {item.dest_rel.lower() for item in asi_plan.items}
            check("ASI-мод: INI остаётся в корне игры", "asimod.ini" in asi_dests, ", ".join(sorted(asi_dests)))

            wrapped = base / "To your scripts folder!!!"
            write(wrapped / "SPAII.dll", b"MZ-SPAII")
            write(wrapped / "SPA II" / "settings.cfg", "x=1")
            wrapped_plan = Analyzer(str(game_dir)).analyze(prepare_source(wrapped))
            wrapped_dests = {item.dest_rel.lower() for item in wrapped_plan.items}
            check(
                "Служебная папка «To your scripts folder» не дублируется",
                "scripts/spaii.dll" in wrapped_dests,
                ", ".join(sorted(wrapped_dests)),
            )
            check(
                "Вложенная папка мода внутри служебной сохраняется",
                "scripts/spa ii/settings.cfg" in wrapped_dests,
                ", ".join(sorted(wrapped_dests)),
            )

            folders_game = workspace / "folders_game"
            write(folders_game / "GTA5.exe", b"MZ")
            install_plans([plan], folders_game, make_backups=False, check_running=False)
            check(
                "Установка создаёт scripts/SinglePlayerApartment/settings.cfg",
                (folders_game / "scripts" / "SinglePlayerApartment" / "settings.cfg").is_file(),
            )
            pending = pending_auto_dependencies([plan], folders_game)
            check("После установки предлагается SHVDN", pending == ["shvdn"], str(pending))
            check(
                "Уже установленная зависимость не предлагается",
                pending_auto_dependencies(
                    [plan], folders_game, installed_names=[DEP_BY_KEY["shvdn"].name]
                )
                == [],
            )

        def test_install() -> None:
            base = workspace / "install_case"
            root = make_mod_tree(base)
            write(game_dir / "TestMod.asi", b"ASI-OLD-ORIGINAL")
            source = prepare_source(root)
            plan = Analyzer(str(game_dir)).analyze(source)
            asi = next((item for item in plan.items if item.dest_rel.lower() == "testmod.asi"), None)
            check("Конфликт с существующим файлом найден", asi is not None and asi.conflict)

            result = install_plans([plan], game_dir, make_backups=True, check_running=False)
            check("Установлено 3 файла", result.file_count == 3, f"получено {result.file_count}")
            check("ASI заменён", (game_dir / "TestMod.asi").read_bytes().startswith(b"ASI-NEW-"))
            check(
                "SHVDN-скрипт установлен в scripts",
                (game_dir / "scripts" / "TestScript.dll").is_file(),
            )
            check("INI установлен", (game_dir / "TestMod.ini").is_file())
            check("Замена учтена в результате", result.replaced == 1, f"замен={result.replaced}")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            check("Манифест отмечен как installed", manifest.get("status") == "installed")
            backup = installer.BACKUPS_DIR / result.session_id / "files" / "TestMod.asi"
            check(
                "Резервная копия содержит старый файл",
                backup.is_file() and backup.read_bytes() == b"ASI-OLD-ORIGINAL",
            )
            sessions = [entry for entry in list_backups() if entry.session_id == result.session_id]
            check(
                "Сессия видна в списке копий",
                len(sessions) == 1 and sessions[0].file_count == 3,
                f"сессий={len(sessions)}",
            )

            rolled = rollback_manifest(result.manifest_path)
            check("Откат без ошибок", rolled.failed == 0, "; ".join(rolled.warnings))
            check(
                "Старый ASI восстановлен",
                (game_dir / "TestMod.asi").read_bytes() == b"ASI-OLD-ORIGINAL",
            )
            check(
                "Новые файлы удалены",
                not (game_dir / "TestMod.ini").exists()
                and not (game_dir / "scripts" / "TestScript.dll").exists(),
            )
            check("Созданная папка scripts удалена", not (game_dir / "scripts").exists())
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            check("Манифест отмечен как rolled_back", manifest.get("status") == "rolled_back")

        def test_installed_mods() -> None:
            library_game = workspace / "library_game"
            write(library_game / "GTA5.exe", b"MZ")
            write(library_game / "keep.txt", "keep")
            write(library_game / "Shared.asi", b"ORIGINAL")

            alpha_root = workspace / "library_alpha" / "AlphaMod"
            write(alpha_root / "Shared.asi", b"ALPHA")
            write(alpha_root / "Alpha.ini", "[alpha]")
            alpha_plan = Analyzer(str(library_game)).analyze(prepare_source(alpha_root))

            beta_root = workspace / "library_beta" / "BetaMod"
            write(beta_root / "Shared.asi", b"BETA")
            write(beta_root / "Beta.ini", "[beta]")
            beta_plan = Analyzer(str(library_game)).analyze(prepare_source(beta_root))

            alpha_result = install_plans(
                [alpha_plan], library_game, make_backups=True, check_running=False
            )
            library = [
                mod
                for mod in list_installed_mods()
                if mod.session_id == alpha_result.session_id
            ]
            check(
                "Установленный мод виден в разделе «Мои моды»",
                len(library) == 1 and library[0].title == alpha_plan.title,
                str([(mod.title, mod.file_count) for mod in library]),
            )
            check(
                "У мода учтены файлы, замены и размер",
                bool(library)
                and library[0].file_count == 2
                and library[0].replaced_count == 1
                and library[0].size > 0,
                str(
                    library[0].file_count
                    if library
                    else "мод не найден"
                ),
            )
            alpha_mod = library[0]
            check("Мод не помечен как зависимость", not alpha_mod.dependency)

            beta_result = install_plans(
                [beta_plan], library_game, make_backups=True, check_running=False
            )
            beta_mod = next(
                (
                    mod
                    for mod in list_installed_mods()
                    if mod.session_id == beta_result.session_id
                ),
                None,
            )
            check("Все установленные моды попадают в список", beta_mod is not None)

            first = remove_installed_mod(alpha_mod.manifest_path, alpha_mod.title)
            check(
                "Перезаписанный файл не удаляется вместе со старым модом",
                (library_game / "Shared.asi").read_bytes() == b"BETA",
            )
            check(
                "Пропуск перезаписанного файла объяснён",
                any("перезаписан" in warning for warning in first.warnings),
                "; ".join(first.warnings),
            )
            check(
                "Свободные файлы старого мода удалены",
                not (library_game / "Alpha.ini").exists(),
            )
            check(
                "Старый мод остаётся в списке из-за общего файла",
                any(mod.title == alpha_plan.title for mod in list_installed_mods()),
            )

            second = remove_installed_mod(beta_mod.manifest_path, beta_mod.title)
            check(
                "Удаление нового мода восстанавливает файл предыдущего",
                (library_game / "Shared.asi").read_bytes() == b"ALPHA",
            )
            check("Файлы нового мода удалены", not (library_game / "Beta.ini").exists())
            check("Удаление нового мода без ошибок", second.failed == 0, "; ".join(second.warnings))

            third = remove_installed_mod(alpha_mod.manifest_path, alpha_mod.title)
            check(
                "Повторное удаление восстанавливает исходный файл",
                (library_game / "Shared.asi").read_bytes() == b"ORIGINAL",
                "; ".join(third.warnings),
            )
            check(
                "Мод исчезает из списка после удаления",
                not any(
                    mod.session_id == alpha_mod.session_id
                    for mod in list_installed_mods()
                ),
            )
            alpha_manifest = json.loads(alpha_mod.manifest_path.read_text(encoding="utf-8"))
            check(
                "Сессия мода отмечена как rolled_back",
                alpha_manifest.get("status") == "rolled_back",
            )
            check(
                "Посторонние файлы игры не затронуты",
                (library_game / "keep.txt").is_file() and (library_game / "GTA5.exe").is_file(),
            )

            group_game = workspace / "library_group_game"
            write(group_game / "GTA5.exe", b"MZ")
            group_a_root = workspace / "library_group_a" / "GroupA"
            write(group_a_root / "GroupA.asi", b"GROUP-A")
            write(group_a_root / "GroupA.ini", "a")
            group_b_root = workspace / "library_group_b" / "GroupB"
            write(group_b_root / "GroupB.asi", b"GROUP-B")
            write(group_b_root / "GroupB.ini", "b")
            group_a_plan = Analyzer(str(group_game)).analyze(prepare_source(group_a_root))
            group_b_plan = Analyzer(str(group_game)).analyze(prepare_source(group_b_root))
            group_result = install_plans(
                [group_a_plan, group_b_plan],
                group_game,
                make_backups=False,
                check_running=False,
            )
            cards = [
                mod
                for mod in list_installed_mods()
                if mod.session_id == group_result.session_id
            ]
            check(
                "Файлы одной сессии разделены по модам",
                {mod.title for mod in cards} == {group_a_plan.title, group_b_plan.title},
                str([mod.title for mod in cards]),
            )
            group_a_mod = next(mod for mod in cards if mod.title == group_a_plan.title)
            remove_installed_mod(group_a_mod.manifest_path, group_a_mod.title)
            check(
                "Удаление одного мода не трогает соседний",
                not (group_game / "GroupA.asi").exists()
                and (group_game / "GroupB.asi").read_bytes() == b"GROUP-B"
                and (group_game / "GroupB.ini").is_file(),
            )
            group_entry = next(
                (
                    entry
                    for entry in list_backups()
                    if entry.session_id == group_result.session_id
                ),
                None,
            )
            check(
                "Счётчик сессии учитывает только оставшийся мод",
                group_entry is not None and group_entry.file_count == 2,
                str(group_entry.file_count) if group_entry else "сессия не найдена",
            )
            group_rollback = rollback_manifest(group_result.manifest_path)
            check(
                "Полный откат совместим с частично удалённым модом",
                group_rollback.failed == 0
                and not (group_game / "GroupB.asi").exists()
                and not (group_game / "GroupB.ini").exists(),
                "; ".join(group_rollback.warnings),
            )
            group_manifest = json.loads(
                group_result.manifest_path.read_text(encoding="utf-8")
            )
            check(
                "Сессия после полного отката закрыта",
                group_manifest.get("status") == "rolled_back",
            )

        def test_manual() -> None:
            base = workspace / "manual_case"
            root = base / "Suspicious"
            write(root / "installer.exe", b"MZ fake")
            write(root / "ReadMe.txt", "Run installer.exe")
            source = prepare_source(root)
            plan = Analyzer(str(game_dir)).analyze(source)
            exe_items = [item for item in plan.items if item.src.suffix.lower() == ".exe"]
            check(
                "EXE отключён в плане",
                len(exe_items) == 1 and exe_items[0].manual and not exe_items[0].enabled,
            )
            check("EXE не получает путь установки", bool(exe_items) and not exe_items[0].dest_rel)
            check("Пакет только с EXE не устанавливается", not plan.installable)
            check(
                "Есть предупреждение о ручной установке",
                any("ручной" in warning for warning in plan.warnings),
            )
            blocked = False
            try:
                install_plans([plan], game_dir, make_backups=False, check_running=False)
            except InstallError:
                blocked = True
            check("Установка такого пакета заблокирована", blocked)
            check("EXE не скопирован в игру", not (game_dir / "installer.exe").exists())

        def test_oiv() -> None:
            base = workspace / "oiv_case"
            root = base / "pack"
            write(root / "files" / "Sample.asi", b"ASI")
            write(root / "files" / "patch.ytd", b"YTD")
            write(
                root / "assembly.xml",
                """<?xml version="1.0" encoding="utf-8"?>
<package>
  <metadata><name>OIV Test Pack</name></metadata>
  <content>
    <add source="files/Sample.asi">Sample.asi</add>
    <archive path="x64a.rpf">
      <add source="files/patch.ytd">x64a.rpf/patch.ytd</add>
    </archive>
  </content>
</package>""",
            )
            package = parse_oiv(root)
            check("Название взято из assembly.xml", package.title == "OIV Test Pack", package.title)
            check(
                "OIV: ASI добавлен в файлы",
                any(entry.dest_rel.lower() == "sample.asi" for entry in package.files),
            )
            check("OIV: операция с RPF требует OpenIV", package.requires_openiv)
            check(
                "OIV: операция архива описана",
                any("архив" in action.lower() for action in package.openiv_actions),
            )
            check("OIV разобран без предупреждений", not package.warnings, "; ".join(package.warnings))

            oiv_zip = zip_tree(base / "Pack.oiv", root)
            source = prepare_source(oiv_zip)
            check("OIV-архив распознан", bool(source.oiv_roots))
            plan = Analyzer(str(game_dir)).analyze(source)
            check(
                "OIV подключён к плану установки",
                any(item.dest_rel.lower() == "sample.asi" for item in plan.items),
            )
            check("OIV добавляет зависимость OpenIV", "openiv" in plan.deps)

        def test_site_resources() -> None:
            catalog = load_catalog()
            check("Встроенный каталог загружен", len(catalog) > 0, f"записей: {len(catalog)}")
            check("Каталог содержит описания", any(item.summary for item in catalog))
            html = """
            <div class="file-list-obj">
              <a class="preview" href="/scripts/sample-mod">
                <img src="https://images.gta5-mods.com/sample.jpg">
              </a>
              <div class="details">
                <div class="name"><a href="/scripts/sample-mod">Sample Mod</a></div>
                <div class="bottom"><a href="/users/author">Author</a></div>
                <div class="version">v1.2</div>
                <ul class="categories"><li>Scripts</li><li>Tools</li></ul>
              </div>
            </div>
            """
            soup = BeautifulSoup(html, "html.parser")
            listing = _card_from_element(soup.select_one(".file-list-obj"))
            check(
                "Карточка мода разобрана",
                listing is not None and listing.title == "Sample Mod",
                listing.title if listing else "не разобрана",
            )
            check(
                "Ссылка карточки абсолютная",
                listing is not None and listing.url == "https://www.gta5-mods.com/scripts/sample-mod",
            )
            check(
                "Автор и версия разобраны",
                listing is not None and listing.author == "Author" and listing.version == "v1.2",
            )
            check("Категория разобрана", listing is not None and "scripts" in listing.tag.lower())
            allowed = False
            try:
                _safe_remote_url("https://www.gta5-mods.com/tools/sample/download")
                allowed = True
            except SiteError:
                allowed = False
            check("Ссылка GTA5-Mods разрешена", allowed)
            blocked = False
            try:
                _safe_remote_url("https://evil.example/file.zip")
            except SiteError:
                blocked = True
            check("Сторонний хост скачивания заблокирован", blocked)

            site_dir = workspace / "site_case"
            fake_html = write(site_dir / "fake.zip", b"<!DOCTYPE html><html>blocked</html>")
            check("HTML-подмена вместо архива отклонена", bool(_check_magic(fake_html, ".zip")))
            real_zip = site_dir / "real.zip"
            with zipfile.ZipFile(real_zip, "w") as zf:
                zf.writestr("a.txt", "a")
            check("Настоящий ZIP принимается", _check_magic(real_zip, ".zip") == "")

            for name in ("icon.png", "icon.ico", "check.svg"):
                check(f"Ресурс assets/{name} доступен", resource_path("assets", name).is_file())
            check("Ресурс data/catalog.json доступен", resource_path("data", "catalog.json").is_file())

        for title, callback in (
            ("Утилиты и безопасность путей", test_utils),
            ("Определение игры", test_game),
            ("Архивы и вложенные пакеты", test_archives),
            ("Анализ модов", test_analyze),
            ("Папки модов и зависимости после установки", test_mod_folders),
            ("Установка, резервные копии и откат", test_install),
            ("Мои моды: список и удаление", test_installed_mods),
            ("Защита от автозапуска EXE", test_manual),
            ("OIV-пакеты", test_oiv),
            ("Сайт и встроенные ресурсы", test_site_resources),
        ):
            section(title, callback)
    except Exception:
        failures += 1
        line()
        line("  [FAIL] Самопроверка прервана исключением:")
        for row in traceback.format_exc().splitlines():
            line(f"         {row}")
    finally:
        for module, name, value in patched:
            try:
                setattr(module, name, value)
            except Exception:
                pass
        shutil.rmtree(workspace, ignore_errors=True)

    elapsed = time.monotonic() - started
    line()
    line("=== Итог ===")
    line(
        f"Проверок: {checks} · успешно: {checks - failures} · ошибок: {failures} · "
        f"время: {elapsed:.2f} с"
    )
    line("РЕЗУЛЬТАТ: ВСЁ В ПОРЯДКЕ" if failures == 0 else "РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ")
    return failures == 0
