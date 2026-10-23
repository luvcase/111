"""CI checks the frozen backend and Tk, not user-granted screen/event permissions."""

import json
import platform
import subprocess
from pathlib import Path

app = Path("dist/ScreenBridge.app")
result = Path("build/mac-smoke.json")
command = [
    str(app / "Contents/MacOS/ScreenBridge"),
    "--package-smoke",
    "--result-path",
    str(result.resolve()),
]
subprocess.run(command, check=True, timeout=90)
report = json.loads(result.read_text())
assert report["ok"] and report["frozen"]
assert report["tk_checked"]
assert platform.machine() == "arm64"
report.update(architecture="arm64", permissions_and_capture_verified=False)
result.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(result.read_text())
