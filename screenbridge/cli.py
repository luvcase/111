import argparse
import getpass
import os
import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import httpx
import qrcode
import uvicorn
from dotenv import load_dotenv

from .app import create_app
from .provider import MODES, Settings


def local_ip():
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        return next(ip[4][0] for ip in addresses if not ip[4][0].startswith("127."))
    except (OSError, StopIteration):
        return "127.0.0.1"


def pairing_files(directory, token, url):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = directory / "pair-token.txt"
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(token)
    qrcode.make(url).save(directory / "phone-qr.png")


def wait_for_server(server, token_file, timeout=30):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                token = token_file.read_text(encoding="utf-8").strip()
                response = client.get(
                    server + "/api/status", headers={"Authorization": "Bearer " + token}
                )
                if response.status_code == 200:
                    return True
            except (OSError, httpx.RequestError):
                pass
            time.sleep(0.5)
    return False


def main():
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="ScreenBridge 截图与手机答案中转")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动服务端与手机页面")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--phone-host", help="手机可访问的电脑局域网 IP")
    serve.add_argument("--demo", action="store_true", help="只验证传输，不调用 AI")
    serve.add_argument("--open", action="store_true", help="在电脑浏览器打开控制台")
    serve.add_argument("--runtime-dir", type=Path, default=Path.cwd() / ".runtime")
    desktop = sub.add_parser("desktop", help="启动电脑全局快捷键截图客户端")
    desktop.add_argument("--server", default="http://127.0.0.1:8765")
    desktop.add_argument("--token-file", type=Path, default=Path.cwd() / ".runtime/pair-token.txt")
    desktop.add_argument("--hotkey", default="<ctrl>+<alt>+s")
    desktop.add_argument("--monitor", type=int, default=1)
    desktop.add_argument("--mode", choices=list(MODES), default=None)
    desktop.add_argument("--region-hotkey", default="<ctrl>+<alt>+a")
    desktop.add_argument("--login", action="store_true", help="使用工作台账号登录截图客户端")
    wait = sub.add_parser("wait", help="等待服务启动并验证本机配对令牌")
    wait.add_argument("--server", default="http://127.0.0.1:8765")
    wait.add_argument("--token-file", type=Path, default=Path.cwd() / ".runtime/pair-token.txt")
    wait.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    if args.command == "serve":
        settings = Settings.from_env(demo=args.demo)
        token = os.getenv("INTERVIEW_PAIR_TOKEN") or secrets.token_urlsafe(24)
        app = create_app(settings, token, data_dir=args.runtime_dir)
        config = uvicorn.Config(
            app, host=args.host, port=args.port, access_log=False, timeout_graceful_shutdown=3
        )
        # Bind before writing pairing files; a second launch must not invalidate a running server.
        bound_socket = config.bind_socket()
        host = args.phone_host or local_ip()
        phone_url = f"http://{host}:{args.port}/#token={quote(token)}"
        pairing_files(args.runtime_dir, token, phone_url)
        print(f"模式：{'演示（不识别图片）' if args.demo else '真实 AI'}")
        print(f"手机基础地址：http://{host}:{args.port}/")
        print(f"配对二维码：{args.runtime_dir / 'phone-qr.png'}")
        print("电脑和手机需在同一 Wi-Fi；在手机页面扫码或输入配对令牌。")
        if args.open:

            def open_when_ready():
                server = f"http://127.0.0.1:{args.port}"
                if wait_for_server(server, args.runtime_dir / "pair-token.txt"):
                    webbrowser.open(server + "/#token=" + quote(token))

            threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            uvicorn.Server(config).run(sockets=[bound_socket])
        finally:
            bound_socket.close()
    elif args.command == "wait":
        if not wait_for_server(args.server, args.token_file, args.timeout):
            parser.exit(1, "服务尚未就绪，请检查服务窗口、端口和配对令牌。\n")
    else:
        from .desktop import run_desktop

        token = (
            args.token_file.read_text(encoding="utf-8").strip()
            if args.token_file.exists()
            else getpass.getpass("配对令牌：")
        )
        try:
            if args.login:
                username = input("账号：").strip()
                password = getpass.getpass("密码：")
                with httpx.Client(timeout=15, trust_env=False) as client:
                    response = client.post(
                        args.server.rstrip("/") + "/api/auth/login",
                        json={"username": username, "password": password},
                    )
                    response.raise_for_status()
                    token = response.json()["token"]
                session_path = args.token_file.parent / "desktop-session.txt"
                fd = os.open(session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as file:
                    file.write(token)
            run_desktop(
                args.server,
                token,
                args.hotkey,
                args.monitor,
                args.mode,
                args.region_hotkey,
                args.token_file.parent / "desktop-session.txt",
            )
        except ImportError:
            parser.error("缺少桌面依赖，请运行 uv sync --extra desktop")
        except Exception as exc:
            parser.exit(1, f"桌面客户端无法启动：{exc}\n")


if __name__ == "__main__":
    main()
