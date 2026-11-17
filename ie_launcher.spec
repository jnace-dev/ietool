# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：ie_launcher.py -> dist\ie_launcher.exe
# 构建命令： .venv\Scripts\python -m PyInstaller --clean --noconfirm ie_launcher.spec

block_cipher = None

a = Analysis(
    ['ie_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('ie_icon.ico', '.')],
    hiddenimports=['win32com.client', 'win32com', 'win32api', 'pywintypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'PIL', 'matplotlib', 'scipy', 'pandas', 'pytest',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython', 'notebook',
        'setuptools', 'pip', 'sqlite3', 'lxml', 'cryptography', 'pydoc_data',
    ],
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
    name='ie_launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ie_icon.ico'],
    # 自定义清单：声明 Per-Monitor V2 的 DPI 感知
    # （不加的话 exe 默认是 DPI-unaware，Windows 会把窗口按位图拉伸，文字发虚）
    manifest='ie_launcher.manifest',
)
