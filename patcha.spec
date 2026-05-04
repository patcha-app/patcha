# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []

for pkg in ['objc', 'Foundation', 'AppKit', 'Quartz', 'ApplicationServices']:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

datas += collect_data_files('tiktoken')
try:
    d, b, h = collect_all('qdrant_client')
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass

hiddenimports += [
    'tiktoken_ext.openai_public',
    'tiktoken_ext',
    'sklearn.utils._cython_blas',
    'sklearn.neighbors._partition_nodes',
    'sklearn.utils._typedefs',
    'qdrant_client.local',
    'qdrant_client.local.local_collection',
    'qdrant_client.local.qdrant_local',
    'qdrant_client.local.payload_filters',
    'qdrant_client.local.persistence',
    'grpc',
]

binaries += [
    ('data/ax_content', 'macos'),
    ('data/ocr', 'macos'),
]
datas += [
    ('patcha/macos/window_title.applescript', 'macos'),
]

a = Analysis(
    ['patcha/cli.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['hooks/'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='patcha',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
