# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Image Explorer — one file for macOS, Windows and Linux.

Build from the project root:
    pyinstaller --noconfirm --clean packaging/image_explorer.spec

Output (in dist/):
    macOS    Image Explorer.app             (folder bundle, ad-hoc signed)
    Windows  Image Explorer/Image Explorer.exe  (+ _internal/ alongside)
    Linux    Image Explorer/Image Explorer

PyInstaller does not cross-compile: build the Mac app on a Mac and the
Windows app on Windows (or use .github/workflows/build.yml).
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # SPECPATH is set by PyInstaller

APP_NAME = "Image Explorer"
BUNDLE_ID = "com.example.imageexplorer"  # TODO: change to your own reverse-DNS id

with open(os.path.join(ROOT, "app", "__init__.py"), encoding="utf-8") as fh:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', fh.read()).group(1)

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# Qt modules we never use. PyInstaller only follows real imports, but excluding
# these stops optional hooks or stray imports dragging in hundreds of MB.
EXCLUDES = [
    "tkinter", "unittest", "pydoc", "PyQt5", "PyQt6", "PySide2",
    "matplotlib", "IPython", "pytest", "pytestqt",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "PySide6.QtSvg", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtDesigner",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets", "icon.png"), "assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,  # strip asserts/docstrings-lite; keeps tracebacks useful
)

# Qt plugins drag in libraries this widgets-only app never uses: the virtual
# keyboard input plugin pulls in QML/Quick, the PDF image plugin pulls in
# QtPdf, and Qt's own translations aren't used. Dropping them saves ~30 MB.
# Patterns match both Linux/Windows libs (Qt6Quick.dll) and macOS frameworks.
TRIM = ("qtquick", "qt6quick", "qtqml", "qt6qml", "virtualkeyboard",
        "qtpdf", "qt6pdf", "qpdf", "/translations/", "\\translations\\")


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    return not any(p.replace("\\", "/") in dest for p in TRIM)


_before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
print(f"[spec] trimmed {_before - len(a.binaries) - len(a.datas)} unused Qt files")

pyz = PYZ(a.pure)

# ----- Windows version resource (shown in Explorer > Properties > Details) ----
version_info = None
if IS_WIN:
    try:
        from PyInstaller.utils.win32.versioninfo import (
            FixedFileInfo, StringFileInfo, StringStruct, StringTable,
            VarFileInfo, VarStruct, VSVersionInfo,
        )

        nums = tuple(int(n) for n in (VERSION.split(".") + ["0", "0", "0"])[:4])
        version_info = VSVersionInfo(
            ffi=FixedFileInfo(filevers=nums, prodvers=nums),
            kids=[
                StringFileInfo([StringTable("040904B0", [
                    StringStruct("CompanyName", "Image Explorer"),
                    StringStruct("FileDescription", APP_NAME),
                    StringStruct("FileVersion", VERSION),
                    StringStruct("InternalName", APP_NAME),
                    StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                    StringStruct("ProductName", APP_NAME),
                    StringStruct("ProductVersion", VERSION),
                ])]),
                VarFileInfo([VarStruct("Translation", [1033, 1200])]),
            ],
        )
    except Exception as exc:  # never fail a build over metadata
        print(f"[spec] WARNING: skipping Windows version info: {exc}")
        version_info = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # one-folder build: fast start-up, fewer antivirus false positives
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX often trips antivirus and breaks Qt DLLs
    console=False,  # GUI app: no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # build for the machine's own architecture
    codesign_identity=os.environ.get("CODESIGN_IDENTITY") or None,
    # entitlements only matter with a real identity (hardened runtime)
    entitlements_file=(
        os.path.join(ROOT, "packaging", "entitlements.plist")
        if IS_MAC and os.environ.get("CODESIGN_IDENTITY") else None
    ),
    icon=os.path.join(ROOT, "assets", "icon.icns" if IS_MAC else "icon.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=os.path.join(ROOT, "assets", "icon.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow light/dark mode
            "LSApplicationCategoryType": "public.app-category.photography",
            "NSHumanReadableCopyright": "",
        },
    )
