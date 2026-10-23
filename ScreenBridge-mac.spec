"""Build on Apple Silicon macOS; includes its own Python/Tk runtime."""
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
        'pynput.keyboard._darwin', 'pynput.mouse._darwin', 'pynput._util.darwin',
        'PIL.ImageTk', 'qrcode.image.pil',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'playwright', 'ruff'], noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name='ScreenBridge',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, target_arch='arm64',
    codesign_identity=None, entitlements_file=None,
)
collection = COLLECT(executable, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='ScreenBridge')
application = BUNDLE(
    collection, name='ScreenBridge.app', icon=None,
    bundle_identifier='io.github.luvcase.screenbridge',
    info_plist={
        'CFBundleShortVersionString': '0.8.0',
        'CFBundleVersion': '0.8.0',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSLocalNetworkUsageDescription': '连接同一局域网内的手机以同步题目复盘。',
        'NSScreenCaptureUsageDescription': '按你的操作截取题图，用于本地复盘及所选模型分析。',
    },
)
