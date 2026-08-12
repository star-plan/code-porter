# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for standalone code-porter binaries (Win / Linux / macOS)."""

from pathlib import Path

# SPECPATH is the directory containing this .spec file (packaging/).
project_root = Path(SPECPATH).resolve().parent
src_dir = project_root / "src"
entry_script = src_dir / "main.py"

a = Analysis(
    [str(entry_script)],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "code_porter",
        "code_porter.cli",
        "code_porter.scanner",
        "code_porter.archive",
        "code_porter.cleaner",
        "code_porter.models",
        "questionary",
        "prompt_toolkit",
        "rich",
        "rich.console",
        "rich.progress",
        "rich.table",
        "typer",
        "click",
        "pathspec",
        "shellingham",
    ],
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
    a.zipfiles,
    a.datas,
    [],
    name="code-porter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
