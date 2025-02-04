# -*- mode: python ; coding: utf-8 -*-

# Apply patch
from openpectus.lang.exec import units
before = """cache_folder = os.path.join(os.path.dirname(__file__), "pint-cache")
ureg = UnitRegistry(cache_folder=cache_folder)"""
after = "ureg = UnitRegistry()"
with open(units.__file__, "r") as f:
    original_contents = f.read()
with open(units.__file__, "w") as f:
    f.write(original_contents.replace(before, after))
    
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('openpectus')
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

# Undo patch
with open(units.__file__, "w") as f:
    f.write(original_contents)
