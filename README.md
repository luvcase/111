# ScreenBridge

Windows 电脑快捷键截图 → OpenAI 图片识别与解答 → 手机浏览器实时接收。

支持简明答案、详细解析、编程题三种模式；手机自动重连并恢复当前服务的最近 30 条记录。截图仅在处理时保存在内存，不写入磁盘；答案随服务退出清除。演示模式不会识别图片。

## Windows 快速开始

1. 安装 **Python 3.12**（安装时启用 Python Launcher），下载本仓库。
2. 双击 `setup-windows.bat`，安装依赖。
3. 将 `.env.example` 复制为 `.env`，填写 `INTERVIEW_AI_KEY`。默认使用 OpenAI `gpt-4.1-mini`；账户需要可用额度及该模型的图片输入权限。`.env` 已被 Git 忽略，不要分享。
4. 双击 `start-windows.bat`，选择真实 AI 或演示模式。脚本打开电脑控制台和配对二维码，并启动截图客户端。
5. 手机与电脑连接同一 Wi-Fi，扫描 `.runtime/phone-qr.png`。二维码包含配对令牌，勿转发。手机也可输入电脑显示的基础地址，并粘贴 `.runtime/pair-token.txt` 中的令牌。
6. 切换到要截图的窗口，按 **Ctrl + Alt + S**。截图发送成功后，手机页面显示处理状态和答案。

首次启动时，在 Windows 防火墙中允许 Python 在**专用网络**访问。手机连接失败时确认 Wi-Fi 未启用客户端隔离；地址必须是电脑局域网 IP，不能是手机的 localhost。如自动选错 IP，用 `--phone-host 192.168.x.x` 指定。

本版使用局域网 HTTP 配对，不要将 8765 端口转发到公网。若跨网络部署，请使用 HTTPS 和 WSS 反向代理。手机 HTTP 页面可能不支持自动复制，此时长按答案手动复制。

## 命令行

推荐使用 uv，安装步骤在 Windows 脚本中自动完成。

```bash
uv sync --frozen --extra desktop
# 终端 1：真实模式。没有 API Key 时不会启动。
uv run --no-sync screenbridge serve --open
# 或仅验证传输，无需 API Key
uv run --no-sync screenbridge serve --demo --open
# 终端 2：在有桌面会话的电脑上运行
uv run --no-sync screenbridge desktop
```

服务端也可运行在 Linux；全局截图依赖有图形桌面的系统。macOS 需要授予屏幕录制及辅助功能权限；Linux 快捷键通常需要 X11。本次云环境没有 Windows 桌面，桌面截图和防火墙需要在 Windows 上实测。

```bash
# 第二块屏幕，自定义快捷键和回答模式
uv run --no-sync screenbridge desktop --monitor 2 --hotkey '<ctrl>+<shift>+x' --mode code
# 明确指定局域网 IP
uv run --no-sync screenbridge serve --phone-host 192.168.1.10
```

手机的回答方式选择只影响手机上传的图片；快捷键截图模式由桌面客户端的 `--mode` 决定。截图默认来自第一块显示器，图片缩放到最长边 1920 像素；字号很小时应放大题目再截图。最多 5 个待处理任务；图片上限 8 MB。

## OpenAI 配置

```dotenv
INTERVIEW_AI_KEY=在这里填写你自己的密钥
INTERVIEW_AI_BASE_URL=https://api.openai.com/v1
INTERVIEW_AI_MODEL=gpt-4.1-mini
```

可以换成支持多模态 Chat Completions 的 HTTPS 兼容服务。密钥只用于服务端请求，不发送到手机。更改 `.env` 后重启服务。默认每次启动生成新配对令牌，手机需重新扫码；可设置至少 24 字符的 `INTERVIEW_PAIR_TOKEN` 固定配对。令牌用于上传和读取所有当前答案。

## 开发与验证

```bash
uv sync --frozen --extra dev
uv run --no-sync pytest
uv run --no-sync ruff check .
```

本环境已预装 Chromium，可对正在运行的演示服务执行手机浏览器烟雾测试：

```bash
uv run --no-sync python scripts/smoke_browser.py --chromium /usr/bin/chromium
```

此测试会真实上传图片，验证 WebSocket 推送、刷新恢复、手机页面尺寸和断开配对，并将无凭据的页面截图保存到 `.runtime/mobile-demo.png`。

后端测试覆盖图片上传、OpenAI 请求格式、鉴权、队列限制、异常脱敏、实时推送和重连恢复。没有 API Key 时只能验证模拟 AI 和演示传输，不能证明真实 OpenAI 识别可用。

`/healthz` 表示服务可用，不表示 AI 账户已通过验证。数据与配对文件在 `.runtime/`，本地凭据在 `.env`，均已忽略；停止服务后答案历史消失。
