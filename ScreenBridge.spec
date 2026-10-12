# Build on Windows: uv run --no-sync pyinstaller --noconfirm --clean ScreenBridge.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / 'scripts' / 'app_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'screenbridge' / 'static'), 'screenbridge/static')]
          + collect_data_files('certifi'),
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.lifespan.on',
        'pynput.keyboard._win32', 'pynput.mouse._win32', 'pynput._util.win32',
        'PIL.ImageTk', 'qrcode.image.pil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'playwright', 'ruff'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name='ScreenBridge',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
bundle = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name='ScreenBridge',
)
