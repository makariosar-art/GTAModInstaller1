from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import Analyzer, SourcePlan, dependencies_missing
from .applog import get_logger
from .deps_defs import DEP_BY_KEY, DepDef
from .dependencies import dependency_states, prepare_dependency_source
from .installer import InstallResult, install_plans
from .utils import ProgressCB, noop_progress

log = get_logger("workflow")

MANUAL_DEP_KEYS = {"lml", "openiv", "gameconfig"}


@dataclass
class DependencyReport:
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    manual: list[DepDef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.failed or self.warnings)


@dataclass
class InstallWorkflowResult:
    install: InstallResult
    dependencies: DependencyReport
    dep_plans: list[SourcePlan] = field(default_factory=list)


def _auto_dep_keys(keys: set[str] | dict[str, list[str]]) -> tuple[list[str], list[DepDef]]:
    auto: list[str] = []
    manual: list[DepDef] = []
    for key in keys:
        dep = DEP_BY_KEY.get(key)
        if dep is None:
            continue
        if dep.auto and key not in MANUAL_DEP_KEYS:
            if key not in auto:
                auto.append(key)
        else:
            if dep not in manual:
                manual.append(dep)
    order = {key: index for index, key in enumerate(DEP_BY_KEY)}
    auto.sort(key=lambda key: order.get(key, 99))
    return auto, manual


def _progress_slice(progress: ProgressCB, start: int, end: int) -> ProgressCB:
    span = max(1, end - start)

    def wrapped(percent: int | None, message: str) -> None:
        if percent is None:
            progress(None, message)
        else:
            progress(start + int(span * max(0, min(100, percent)) / 100), message)

    return wrapped


def install_workflow(
    mod_plans: list[SourcePlan],
    game_path: Path | str,
    dependency_keys: set[str] | dict[str, list[str]],
    *,
    make_backups: bool = True,
    check_running: bool = True,
    progress: ProgressCB = noop_progress,
    cancel: threading.Event | None = None,
) -> InstallWorkflowResult:
    game = Path(game_path)
    report = DependencyReport()
    auto_keys, manual_deps = _auto_dep_keys(dependency_keys)
    report.manual.extend(manual_deps)
    for dep in manual_deps:
        report.warnings.append(f"{dep.name}: установите вручную — {dep.url}")

    dep_plans: list[SourcePlan] = []
    prepared_sources = []
    queued = list(auto_keys)
    seen: set[str] = set(auto_keys)
    index = 0
    batch = max(1, len(auto_keys))
    states = dependency_states(game)
    try:
        while queued:
            if cancel is not None and cancel.is_set():
                from .installer import InstallCancelled

                raise InstallCancelled("Установка отменена.")
            key = queued.pop(0)
            index += 1
            dep = DEP_BY_KEY.get(key)
            if dep is None:
                continue
            state = states.get(key)
            if state is not None and state.installed is True:
                report.skipped.append(dep.name)
                continue
            start = int(45 * (index - 1) / batch)
            end = int(45 * index / batch)
            stage_progress = _progress_slice(progress, start, end)
            progress(None, f"Зависимость {index}: {dep.name}")
            try:
                source, acquisition = prepare_dependency_source(
                    key,
                    progress=stage_progress,
                    cancel=cancel,
                )
                plan = Analyzer(str(game)).analyze(source, stage_progress)
                if not plan.installable:
                    report.failed.append((dep.name, "В пакете не найдено файлов для установки."))
                    source.cleanup()
                    continue
                dep_plans.append(plan)
                prepared_sources.append(source)
                report.installed.append(dep.name)
                log.info("Dependency prepared: %s (%s)", key, acquisition.archive.name)
                for needed in dependencies_missing([plan]):
                    if needed in seen or needed in MANUAL_DEP_KEYS:
                        continue
                    dep_needed = DEP_BY_KEY.get(needed)
                    if dep_needed is None or not dep_needed.auto:
                        continue
                    needed_state = states.get(needed)
                    if needed_state is not None and needed_state.installed is True:
                        continue
                    if needed not in queued:
                        queued.append(needed)
                        seen.add(needed)
                        batch = max(batch, len(seen))
            except Exception as exc:  # noqa: BLE001 - reported per dependency
                if cancel is not None and cancel.is_set():
                    from .installer import InstallCancelled

                    raise InstallCancelled("Установка отменена.") from exc
                log.warning("Dependency failed: %s: %s", key, exc)
                report.installed = [name for name in report.installed if name != dep.name]
                report.failed.append((dep.name, str(exc)))

        all_plans = dep_plans + mod_plans
        install_progress = _progress_slice(progress, 45 if auto_keys else 0, 100)
        progress(None, "Установка файлов в игру…")
        result = install_plans(
            all_plans,
            game,
            make_backups=make_backups,
            check_running=check_running,
            progress=install_progress,
            cancel=cancel,
        )
        log.info(
            "Installed %s files (%s bytes), replaced %s, session %s",
            result.file_count,
            result.total_bytes,
            result.replaced,
            result.session_id,
        )
        return InstallWorkflowResult(install=result, dependencies=report, dep_plans=dep_plans)
    finally:
        for source in prepared_sources:
            source.cleanup()


def pending_auto_dependencies(
    plans: list[SourcePlan],
    game_path: Path | str | None,
    *,
    installed_names: set[str] | list[str] | tuple[str, ...] = (),
) -> list[str]:
    states = dependency_states(game_path) if game_path else {}
    done = set(installed_names)
    auto_keys, _manual = _auto_dep_keys(dependencies_missing(plans))
    pending: list[str] = []
    for key in auto_keys:
        dep = DEP_BY_KEY.get(key)
        if dep is None or dep.name in done:
            continue
        state = states.get(key)
        if state is not None and state.installed is True:
            continue
        pending.append(key)
    return pending


def install_dependencies(
    keys: set[str] | list[str],
    game_path: Path | str,
    *,
    make_backups: bool = True,
    check_running: bool = True,
    progress: ProgressCB = noop_progress,
    cancel: threading.Event | None = None,
) -> InstallWorkflowResult:
    return install_workflow(
        [],
        game_path,
        set(keys),
        make_backups=make_backups,
        check_running=check_running,
        progress=progress,
        cancel=cancel,
    )


def manual_dependency_links(keys: set[str]) -> list[DepDef]:
    result: list[DepDef] = []
    for key in keys:
        dep = DEP_BY_KEY.get(key)
        if dep is not None and not dep.auto and dep not in result:
            result.append(dep)
    return result
