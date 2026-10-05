# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

project_root = Path(SPECPATH).resolve()
sys.path.insert(0, str(project_root))

from src.config import DEFAULT_FRAME_RESOURCE, GCASH_QR_RESOURCE, ICON_RESOURCE


def bundled_data(resource: Path | str) -> tuple[str, str]:
    relative_path = Path(resource)
    if relative_path.is_absolute():
        raise ValueError(f"Bundled resource must be a relative path: {resource}")
    source_path = project_root / relative_path
    if not source_path.is_file():
        raise FileNotFoundError(f"Bundled resource does not exist: {source_path}")
    return str(source_path), relative_path.parent.as_posix()

block_cipher = None

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        bundled_data(ICON_RESOURCE),
        bundled_data(DEFAULT_FRAME_RESOURCE),
        bundled_data(GCASH_QR_RESOURCE),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoPost Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon="app_icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AutoPost Studio",
)
