"""Bound native GUI tests and retain diagnostics when Cocoa stops returning."""

import json
import os
import subprocess
import sys
from pathlib import Path

output = Path("build/mac-source-check.json")
output.parent.mkdir(exist_ok=True)
result = {"ok": False}
command = [
    sys.executable,
    "-c",
    "import faulthandler,pytest,sys; faulthandler.dump_traceback_later(30,repeat=True); "
    "sys.exit(pytest.main(sys.argv[1:]))",
    "-vv",
    "-s",
    "-x",
    "--junitxml=build/mac-tests.xml",
]
with Path("build/mac-source.log").open("w", encoding="utf-8") as log:
    try:
        process = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=90,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        result.update(ok=process.returncode == 0, returncode=process.returncode)
    except subprocess.TimeoutExpired:
        result["error"] = "Mac source tests did not finish within 90 seconds"
result["output"] = Path("build/mac-source.log").read_text(encoding="utf-8", errors="replace")[
    -16000:
]
output.write_text(json.dumps(result), encoding="utf-8")
print(result["output"])
raise SystemExit(0 if result["ok"] else 1)
