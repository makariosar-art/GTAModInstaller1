from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DepDef:
    key: str
    name: str
    subtitle: str
    description: str
    group: str
    auto: bool
    url: str


DEPS: tuple[DepDef, ...] = (
    DepDef(
        key="shv",
        name="Script Hook V",
        subtitle="ASI Loader · база для .asi модов",
        description="Загрузчик ASI-плагинов и библиотека ScriptHookV от Alexander Blade. Без него не работают тренеры, .asi-скрипты и большинство модов.",
        group="Основа",
        auto=True,
        url="http://www.dev-c.com/gtav/scripthookv/",
    ),
    DepDef(
        key="shvdn",
        name="Script Hook V .NET",
        subtitle="SHVDN 3 · для .dll и .cs скриптов",
        description="Позволяет запускать C#-скрипты и меню. Нужен для .dll/.cs/.vb в папке scripts и для большинства современных модов.",
        group="Основа",
        auto=True,
        url="https://github.com/scripthookvdotnet/scripthookvdotnet/releases",
    ),
    DepDef(
        key="lemonui",
        name="LemonUI",
        subtitle="Библиотека интерфейса",
        description="Современная UI-библиотека для SHVDN-скриптов. Нужна, если мод использует LemonUI (часто в новых меню и тренерах).",
        group="Интерфейс",
        auto=True,
        url="https://github.com/LemonUIbyLemon/LemonUI/releases",
    ),
    DepDef(
        key="nativeui",
        name="NativeUI",
        subtitle="Библиотека меню",
        description="Классическая библиотека меню для SHVDN-скриптов. Требуется старым модам с меню на NativeUI.",
        group="Интерфейс",
        auto=True,
        url="https://github.com/Guad/NativeUI/releases",
    ),
    DepDef(
        key="lua",
        name="Lua Plugin",
        subtitle="Загрузчик .lua скриптов",
        description="Плагин Script Hook V для запуска Lua-скриптов. Нужен для модов с файлами .lua.",
        group="Моды и загрузчики",
        auto=True,
        url="https://www.gta5-mods.com/tools/lua-plugin-for-script-hook-v",
    ),
    DepDef(
        key="lml",
        name="Lenny's Mod Loader",
        subtitle="Загрузчик модов LML",
        description="Позволяет устанавливать моды через папку lml без изменения RPF. Установите вручную со страницы LML.",
        group="Моды и загрузчики",
        auto=False,
        url="https://www.lcpdfr.com/downloads/gta5mods/misc/25437-lennys-mod-loader/",
    ),
    DepDef(
        key="openiv",
        name="OpenIV",
        subtitle="RPF, dlclist и правки игры",
        description="Нужен для модов, которые изменяют RPF-архивы, добавляют DLC и текстовые данные. Скриптовые моды работают без него.",
        group="RPF и правки игры",
        auto=False,
        url="https://openiv.com/",
    ),
    DepDef(
        key="gameconfig",
        name="Gameconfig",
        subtitle="Лимиты транспорта и текстур",
        description="Патч лимитов игры для тяжёлых сборок и большого количества add-on транспорта. Устанавливается через OpenIV.",
        group="RPF и правки игры",
        auto=False,
        url="https://www.gta5-mods.com/misc/gta-5-gameconfig-300-cars",
    ),
    DepDef(
        key="heap",
        name="Heap Adjuster",
        subtitle="Увеличение heap-памяти",
        description="Помогает избежать вылетов при большом количестве модов и высоком потреблении памяти.",
        group="Оптимизация",
        auto=True,
        url="https://www.gta5-mods.com/tools/heapadjuster",
    ),
    DepDef(
        key="packfile",
        name="Packfile Limit Adjuster",
        subtitle="Лимиты RPF-архивов",
        description="Поднимает лимиты packfile — нужен для больших сборок транспорта и карт.",
        group="Оптимизация",
        auto=True,
        url="https://www.gta5-mods.com/tools/packfile-limit-adjuster",
    ),
)

DEP_BY_KEY = {dep.key: dep for dep in DEPS}

DEP_GROUP_ORDER = ("Основа", "Интерфейс", "Моды и загрузчики", "RPF и правки игры", "Оптимизация")


def dep_name(key: str) -> str:
    dep = DEP_BY_KEY.get(key)
    return dep.name if dep else key
