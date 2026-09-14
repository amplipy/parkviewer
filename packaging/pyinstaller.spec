# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ParkViewer desktop app (used on all three OSes).

Build:  pyinstaller packaging/pyinstaller.spec
Output: dist/ParkViewer/  (one-dir bundle)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# SPECPATH is the directory containing this spec file (packaging/).
ROOT = Path(SPECPATH).resolve().parent  # repo root
os.chdir(ROOT)

if not (ROOT / "app_entry.py").exists():
    raise SystemExit(f"spec error: app_entry.py not found under {ROOT}")

block_cipher = None

datas = [
    (str(ROOT / "park_tiff_viewer.py"), "."),
    (str(ROOT / "park_tiff_core.py"), "."),
    (str(ROOT / "packaging" / "assets" / "icon_64.png"), "packaging/assets"),
    (str(ROOT / "packaging" / "assets" / "icon_256.png"), "packaging/assets"),
]

# Streamlit needs its package data (frontend static assets) AND its dist-info
# metadata (streamlit/version.py calls importlib.metadata.version at runtime).
datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")
# Other runtime deps that may resolve metadata/entry points when frozen.
for _dist in [
    "altair",
    "pyarrow",
    "pandas",
    "numpy",
    "pillow",
    "matplotlib",
    "matplotlib-scalebar",
    "python-pptx",
    "streamlit-image-select",
    "packaging",
    "click",
    "jsonschema",
    "typing_extensions",
    "watchdog",
]:
    try:
        datas += copy_metadata(_dist)
    except Exception as _e:  # noqa: BLE001 - name not installed on this OS
        print(f"spec: skipping metadata for {_dist} ({_e})")

is_win = sys.platform.startswith("win")
is_mac = sys.platform == "darwin"

hiddenimports = [
    "streamlit",
    "streamlit_image_select",
    # streamlit lazy-imports these at runtime:
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.web.cli",
    "streamlit.runtime.websocket.server",
    "altair",
    "pyarrow",
    "numpy",
    "PIL",
    "PIL._tkinter_finder",
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "matplotlib_scalebar.scalebar",
    "pptx",
    "webview",
    "park_tiff_core",
]

# pywebview picks its GUI backend dynamically; on Linux that is Qt WebEngine
# and the dynamic import is invisible to the module graph.
if not (is_win or is_mac):
    hiddenimports += [
        "webview.platforms.qt",
        "qtpy",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtWebChannel",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
    ]

a = Analysis(
    [str(ROOT / "app_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

is_win = sys.platform.startswith("win")
is_mac = sys.platform == "darwin"

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ParkViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep console on all platforms: log output is the only
    #                 visible feedback if the server fails to start.
    icon=str(ROOT / "packaging" / "assets" / "icon.ico") if is_win else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="ParkViewer",
)

if is_mac:
    app = BUNDLE(
        coll,
        name="ParkViewer.app",
        icon=str(ROOT / "packaging" / "assets" / "icon.icns"),
        bundle_identifier="com.parkviewer.app",
        info_plist={
            "CFBundleName": "ParkViewer",
            "CFBundleDisplayName": "ParkViewer",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.developer-tools",
        },
    )