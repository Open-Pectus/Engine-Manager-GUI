# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('openpectus')
datas += collect_data_files("pint", includes=["default_en.txt", "constants_en.txt"])
datas += [
    ('openpectus_engine_manager_gui/icon.ico', './'),
    ('openpectus_engine_manager_gui/icon.png', './'),
]

a = Analysis(
    ['openpectus_engine_manager_gui/__init__.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Open Pectus Engine Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['openpectus_engine_manager_gui/icon.ico'],
)
