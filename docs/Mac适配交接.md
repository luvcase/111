# ScreenBridge：Mac 版本开发交接

> 本文记录 0.7.0 时的交接基线。0.8.0 已新增 Apple 芯片适配及打包流程；当前使用步骤与验证边界请参阅 [Mac 应用使用说明](Mac应用使用说明.txt)。下文“当前”指交接基线状态。

这份文档和仓库源码可以直接交给负责 Mac 适配的开发者或 AI。无需从 Windows exe 反编译。当前应用版本为 0.7.0，交接基线为 `de3650897646a43858b419bf2a8b0b9195ce78cf`。

仓库：https://github.com/luvcase/111

**当前没有交付或验证 macOS 安装包。** 现有 Windows 包不能直接用于 Mac，已有跨平台代码也不等于完整的 Mac 支持。本文的 Mac 启动命令供接手者在 Mac 上验证，本次交接未执行这些 Mac 检查。

## 一、需要提供的东西

- 本仓库完整源码，包含 `pyproject.toml`、`uv.lock`、`screenbridge/`、`scripts/`、`tests/` 和文档。GitHub 的 Code → Download ZIP 是源码；Actions 中的 Windows artifact 是应用，两者不同。
- 目标 Mac 的芯片型号（Apple Silicon：M1/M2/M3/M4 等，或 Intel）及 macOS 版本。构建目标需要匹配，建议分别产出 arm64 和 x86_64 应用，不假设一个包可在所有机器原生运行。
- 一台对应架构的 Mac 或 macOS 构建环境。最终截图、快捷键和系统权限需要真实登录的桌面会话验证。
- 接手者自己的模型 API Key，仅真实模型测试需要。演示服务、本地读格及多数自动测试不需要真实 Key。

不要转发 `.env`、`.runtime/`、Windows 的 `%LOCALAPPDATA%\ScreenBridge` 数据目录，或包含密钥、会话、题图与记录的数据库。仓库的 `.env.example` 是空值配置示例，可以保留。若仓库不可公开读取，请由仓库所有者分享下载的源码 ZIP 或授权访问，不需分享 GitHub 密码。

## 二、技术结构和文件入口

这是 Python 应用，不是 Electron/Tauri 项目。当前不需要 Node.js 构建前端。

| 文件 | 用途及 Mac 适配重点 |
| --- | --- |
| `screenbridge/launcher.py` | Tk 启动器、服务启动、原生子进程、单实例锁和退出清理。`data_directory()` 当前非 Windows 分支使用 XDG 路径，Mac 建议改用 `~/Library/Application Support/ScreenBridge`。冻结后的子进程启动须在 `.app` 中验证。 |
| `screenbridge/desktop.py` | mss 截图、Tk 冻结截图选区、pynput 全局快捷键及可选鼠标长按。重点验证屏幕权限、事件监听、Retina 缩放、多显示器及坐标映射。Windows DPI 调用已有平台分支。 |
| `screenbridge/app.py` | FastAPI 服务、账号空间、题图、任务与手机同步。可复用，不需要重写服务架构。 |
| `screenbridge/provider.py` | OpenAI 兼容模型接口、模型列表、超时、用量和输出处理。可复用；模型名称预设不代表真实访问权限。 |
| `screenbridge/grids.py`、`images.py` | 本地黑白网格读格、矩阵和检查图、图片编码。仅辅助读图，不保证模型推理正确。 |
| `screenbridge/store.py` | SQLite 数据、账号与会话。不要随源码附带用户数据库。 |
| `screenbridge/static/` | 已可直接运行的 HTML/CSS/JS 工作台，电脑、手机共用。 |
| `scripts/app_entry.py` | PyInstaller 应用入口及启动异常处理。 |
| `ScreenBridge.spec` | **Windows 专用打包参考**：包含 `_win32` 隐式导入，当前未定义 macOS `BUNDLE`，不能原样当作 Mac 打包配置。 |
| `.github/workflows/` | 现有测试覆盖 Ubuntu/Windows，应用打包仅 Windows；需增加 macOS 任务。 |

Python 要求为 >=3.11，现有 CI 使用 3.12。核心依赖是 FastAPI、Uvicorn、httpx、Pillow、python-multipart、python-dotenv、qrcode；桌面额外依赖 mss、pynput；打包依赖 PyInstaller。版本范围和实际锁定版本以清单及锁文件为准。

## 三、接手后先在 Mac 验证源码

准备带有可用 Tcl/Tk 的 Python 3.12 及 uv，在项目根目录执行：

```bash
uv sync --frozen --python 3.12 --extra desktop --extra dev --extra build
uv run --no-sync python -c 'import tkinter; print(tkinter.TkVersion)'
uv run --no-sync ruff check .
uv run --no-sync pytest -q
```

如果 Tk 导入失败，应先修复 Python/Tcl/Tk 安装；不要将命令通过视为桌面 GUI 已可工作。

先只启动演示服务，验证浏览器界面：

```bash
uv run --no-sync screenbridge serve --demo --host 127.0.0.1 --open
```

结束后再启动完整应用，避免同一服务端口冲突：

```bash
uv run --no-sync screenbridge-app
```

完整应用的默认真实模式需要在模型配置中填写自己的 Key；这与演示服务只检查传输不同。GUI、屏幕截图和全局快捷键能否正常运行，必须在目标 Mac 上检查。现有浏览器检查脚本的 Chromium 路径默认是 Linux 路径，在 Mac 上运行应通过 `--chromium` 指定当地可用的浏览器可执行文件。

## 四、必须完成的 Mac 适配

1. **系统权限**：处理屏幕录制、辅助功能/输入监控等与所用系统 API 对应的授权；系统版本不同，设置项名称可能不同。缺权限时明确提示如何授权和重启。Terminal/Python 获权不代表打包后的 `.app` 自动获权，要单独测试最终应用。
2. **截图与选区**：验证 Retina 实际像素与 Tk 窗口逻辑坐标、跨屏偏移和负坐标。检查全屏、区域、取消、外接显示器及不同缩放下的结果。
3. **快捷键**：现有默认全屏 Alt+S、选区 Ctrl+Alt+A。Mac 上分别涉及 Option/Control 键；需检查输入法及系统快捷键冲突，并提供合适的 Mac 文案。右键长按是可选功能，需独立测试。
4. **存储与启动**：使用适合 Mac 的数据路径，保留升级后的数据，验证单实例锁、服务就绪、子进程退出、日志及关闭后是否残留进程。
5. **打包**：新增 Mac spec，保留静态资源、certifi、Tk、Pillow、qrcode 和实际需要的 pynput Darwin 后端；创建 `.app` 的 BUNDLE，设置稳定的 bundle identifier、必要权限说明及正确架构。应在 Mac 上构建，不能将现有 Linux/Windows 二进制改扩展名交付。
6. **分发**：个人测试包和公开分发包分开说明。正式对外分发如需通过系统信任检查，应使用自己的 Apple Developer ID 签名并公证；不要把开发者证书或私钥写进源码包。
7. **Mac 验收**：补充 macOS CI，并在真实桌面完成权限授予/拒绝、截图、热键、启动退出和手机连接测试。自动测试通过不能代替这些交互检查。

特别注意：`launcher.py` 的 `package_smoke()` 当前只在 `sys.platform == "win32"` 时默认执行原生截图检查。在 Mac 上直接运行该入口，即使返回 `ok: true`，也可能只是测试图片上传流程；必须检查 `native_checked`，并适配 Mac 的原生截图验收，不能据此宣称截图已验证。

手机同步依赖同一服务可达的局域网地址。应测试 Mac 防火墙及目标系统涉及的本地网络权限；不要把只监听 127.0.0.1 的演示服务当成手机已能连接。

## 五、功能和验证边界

尽量保留已有的导图、OCR、模型配置、本地读格、答案展示、手机同步和复盘记录功能。图形推理错误与接口超时仍是待验证问题，不能因为换到 Mac 就宣称已解决。模型实际可用性及费用以提供商账号为准。

接手者最终应交付：对应芯片的 `.app`（或封装它的 ZIP/DMG）、安装与授权说明、目标 macOS 版本、实测结果及未解决问题。源码交接不代表已交付这些 Mac 成品。
