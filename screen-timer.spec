# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
import sys
import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # ui/_* are local design-harness files (a preview page and a dump of
        # real usage data). Globbed in explicitly so they can never ride along
        # into a shipped build.
        *[
            (os.path.join('ui', f), 'ui')
            for f in os.listdir('ui')
            if not f.startswith('_')
        ],
        ('assets', 'assets'),
        # pywebview's js/*.js ARE the Python<->JS bridge: without them
        # window.pywebview.api is never injected, so the UI loads, renders its
        # static markup, and then sits there permanently blank with no error.
        # The stock hook pulled in pywebview's DLLs but not this JavaScript.
        *collect_data_files('webview'),
    ],
    hiddenimports=[
        'webview',
        'pystray',
        'psutil',
        'PIL',
        'winrt.windows.ui.notifications',
        'winrt.windows.data.xml.dom',
        'win32api',
        'win32con',
        'win32gui',
        'icon_art',
        'paths',
        'runtime',
        'storage',
        'active_window',
        'api',
        'idle',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pulled in transitively but never used at runtime. Deliberately does NOT
    # exclude setuptools/pkg_resources (PyInstaller's own runtime hooks import
    # them), xml (winrt toast building), or email/http (bottle, via pywebview).
    excludes=[
        'tkinter',
        'unittest',
        'doctest',
        'pydoc',
        'pdb',
        'lib2to3',
        'sqlite3',
        'curses',
        'numpy',
        'matplotlib',
        'pandas',
        'PyQt5',
        'PySide2',
        'PIL.ImageQt',
        'PIL.ImageShow',
    ],
    excludedimports=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ScreenTimer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon='assets/icon.ico',
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
