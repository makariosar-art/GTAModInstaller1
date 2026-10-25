# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)

# Qt/third-party binaries that this app never loads. PyInstaller's Qt hook pulls
# them in through optional plugins, so they have to be filtered out explicitly.
BLOCKED_BINARIES = (
    "PySide6/opengl32sw.dll",  # software OpenGL fallback, only used by Qt Quick
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6Qml",
    "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Network.dll",  # network stack is requests/urllib3
    "PySide6/Qt6OpenGL.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/plugins/platforminputcontexts/",
    "PySide6/plugins/tls/",
    "PySide6/plugins/networkinformation/",
    "PySide6/plugins/platforms/qdirect2d.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",
    "PySide6/plugins/platforms/qminimal.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/imageformats/qtiff.dll",
    "PySide6/plugins/imageformats/qicns.dll",
    "PySide6/plugins/imageformats/qtga.dll",
    "PySide6/plugins/imageformats/qwbmp.dll",
    "PySide6/plugins/generic/",
    # Python's own OpenSSL lives in DLLs/; these are Git-for-Windows copies that
    # the dependency scanner picked up from PATH.
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
)
BLOCKED_DATA = ("PySide6/translations/",)

a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(project / "assets"), "assets"),
        (str(project / "data"), "data"),
    ],
    hiddenimports=[
        "modinstaller.selftest",
        "PySide6.QtSvg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebChannel",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtOpenGL",
        "setuptools",
        "pkg_resources",
        "distutils",
        "packaging",
        "pydoc",
        "pydoc_data",
        "doctest",
        "unittest",
        "asyncio",
        "concurrent",
        "pdb",
        "_pyrepl",
        "_pydecimal",
        "pygame",
    ],
    noarchive=False,
    optimize=2,
)

# Qt's hooks collect every plugin that depends on Qt6Gui, which drags Qt Quick,
# Qt Pdf and the virtual keyboard back in. Drop them before packaging.
a.binaries = [
    entry for entry in a.binaries
    if not any(entry[0].replace("\\", "/").startswith(prefix) for prefix in BLOCKED_BINARIES)
]
a.datas = [
    entry for entry in a.datas
    if not entry[0].replace("\\", "/").startswith(BLOCKED_DATA)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GTAModInstaller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project / "assets" / "icon.ico"),
    version=str(project / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GTAModInstaller",
)
