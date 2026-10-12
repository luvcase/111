"""PyInstaller entry point. Do not require a globally installed Python interpreter."""

from screenbridge.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
