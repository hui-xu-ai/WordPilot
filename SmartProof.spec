# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/assets/icon_float.png', 'assets'),
        ('src/assets/tray_icon.png', 'assets'),
        # 只打包配置模板；真实配置（含 API Key）由程序首次运行时在 exe 同级 config/ 自动生成
        ('src/config/MWindow_config.example.json', 'config'),
        ('src/config/navigator_config.example.json', 'config'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pip',
        'setuptools',
        'wheel',
        'pkg_resources',
        'Cython',
        'pipdeptree',
        'pipreqs',
        'distutils',
        'unittest',
        'test',
        'pydoc',
        'tkinter',
        'xmlrpc',
        'lib2to3',
        'ensurepip',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)   # 必须保留这一行

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SmartProof',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/assets/icon.ico',      # 如果 icon.ico 在根目录，如果也在 src 内则改为 'src/icon.ico'
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartProof',
)