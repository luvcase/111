"""PyInstaller entry point. Do not require a globally installed Python interpreter."""

import json
import sys
import traceback
from pathlib import Path


def run():
    result_path = None
    if "--package-smoke" in sys.argv:
        result_path = Path("packaged-smoke.json")
        if "--result-path" in sys.argv:
            result_path = Path(sys.argv[sys.argv.index("--result-path") + 1])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps({"ok": False, "stage": "importing_launcher"}), encoding="utf-8"
        )
    try:
        from screenbridge.launcher import main

        return main()
    except Exception as exc:
        diagnostic = {
            "ok": False,
            "stage": "startup_error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        if result_path is not None:
            result_path.write_text(json.dumps(diagnostic, ensure_ascii=False), encoding="utf-8")
        if "--native" in sys.argv and "--data-dir" in sys.argv:
            directory = Path(sys.argv[sys.argv.index("--data-dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "native-startup-error.log").write_text(
                diagnostic["traceback"], encoding="utf-8"
            )
        elif result_path is None:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
