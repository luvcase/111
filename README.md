# ScreenBridge

Windows 截图与题目复盘工作台：题图直接交给视觉模型，电脑、网页和手机查看同一份复盘内容。

## 普通使用：Windows 应用版，不用安装 Python

1. 打开 [Windows 应用打包页面](https://github.com/luvcase/111/actions/workflows/windows-app.yml)，点击最新的绿色成功记录。
2. 在页面下方 **Artifacts** 下载 **ScreenBridge-Windows-app**。这才是应用包；仓库 **Code → Download ZIP** 下载的是源码。
3. 解压所有文件，双击 **ScreenBridge.exe**。保留旁边的 `_internal` 文件夹，软件会自动启动并打开工作台。
4. 在“模型与提示词”点击 **更换模型 · Astra / Sol**，选择模型预设、填写自己的 OpenAI Key，点击 **保存并应用** 后测试连接。已有配置的 Key 可以留空保留。
5. 用 **Ctrl + Alt + A** 选区截图，或 **Alt + S** 全屏截图；也可导入题图。手机与电脑连接同一 Wi-Fi，在应用启动窗口点击“手机配对二维码”扫码。

应用版自带 Python 运行环境，不需要安装 Python、uv，也不需要运行 bat 脚本。关闭浏览器页面不会停止软件；从启动窗口选择“退出软件”。演示模式只验证界面与传输。

应用版数据保存在 `%LOCALAPPDATA%\ScreenBridge`，源码版数据在 `.runtime/`。应用包未做代码签名，Windows 首次打开可能显示未知发布者提示。Windows 10/11 64 位为目标平台。

以下 Python 安装与命令行说明面向源码开发；如果你只是使用软件，可以跳过。

## 功能

答案现在优先展示；题意、解析与模型信息默认收起，手机收到新结果后自动定位到结果区域。OCR 任务仍直接显示转录文字。全屏快捷键为 **Alt + S**。

- **截图**：Alt + S 全屏、Ctrl + Alt + A 区域选框；工作台可开启右键长按 0.6 秒拖动选区，松开发送，Esc 取消。默认选择第一个显示器，可指定 `--monitor`。
- **题目输入**：导入 PNG/JPEG/WebP、本地裁剪、文字题目和补充条件；不必先复制题干即可分析图片。
- **OCR**：使用所选视觉模型转录文字，保留段落、选项、代码和公式；与直接分析分开操作。
- **题型**：自动识别、选择题、图形题、编程题、数学题。模型能力与图片清晰度决定分析质量。
- **申论**：选择“申论（需给定材料）”或“申论作答”，按概括、分析、对策、应用文、文章写作等题型组织答案，遵守题设格式和字数要求。需要完整给定材料，不会自动读取题图之外的页面。详见 [申论作答说明](docs/申论作答说明.md)。
- **模型配置**：最多保存 10 套 OpenAI 兼容的 HTTPS Chat Completions 配置，单独编辑、删除和测试连接；密钥不会通过列表接口返回。
- **多模型对照**：选择 1–3 套配置并行分析，同屏比较答案、关键步骤、注意事项及耗时。一套失败不会丢掉其他模型的结果。
- **协同建议**：可选额外调用第一个选定模型，对照独立结果、指出分歧并生成综合建议。
- **提示词库**：速答、详细解析、编程、选项、图形风格，以及内置和自定义提示词收藏。
- **账号同步**：同一服务下登录同一账号，跨电脑、网页、手机同步模型选择、提示词、当前复盘与历史；不同账号的内容隔离。也可直接使用设备配对空间。
- **本地保存**：每个空间最近 30 条复盘、题图与配置写入本机 SQLite，重启后保留。可删除记录及题图。

## 源码版 Windows 开始步骤

1. 安装 **Python 3.12**，保留安装器中的 Python Launcher 和 Tcl/Tk 选项，下载并解压仓库。
2. 双击 `setup-windows.bat` 安装依赖。
3. 双击 `start-windows.bat`，选择真实模式或演示模式。打开工作台后，在“模型与提示词”编辑默认配置并填写自己的 OpenAI Key；也可复制 `.env.example` 为 `.env` 后填写 `INTERVIEW_AI_KEY`。默认模型为 `gpt-4.1-mini`，需要账户额度和图片输入权限。
4. 手机与电脑连接同一 Wi-Fi，扫描 `.runtime/phone-qr.png`；或输入服务窗口显示的基础地址，再填写 `.runtime/pair-token.txt` 中的令牌。
5. 用快捷键截图，或导入题图。选择模型和风格，点击“生成解答”或“OCR 转文字”。工作台截图按钮会延迟 2 秒，留出切换题目窗口的时间。
6. 开启右键长按后，按住右键等待选框出现，再拖动并松开；操作与正常右键菜单可能冲突，可随时关闭此功能。

首次启动时在 Windows 防火墙中允许 Python 在**专用网络**访问。连接失败时检查 Wi-Fi 客户端隔离设置；手机必须访问电脑的局域网 IP。如自动识别错误，用 `--phone-host 192.168.x.x` 指定。

浏览器和手机均使用同一个工作台。截图按钮需要 Windows 原生客户端在线；没有客户端时会明确提示，不会将按钮点击当作截图成功。

## 账号与截图客户端

先配对设备，在右上角注册账号，再在其他设备连接同一服务并登录。账号名使用英文、数字或 `_.@-`，密码至少 10 位；账号会话 7 天后需重新登录。密码通过 scrypt 加盐散列保存。

设备配对空间与账号空间分别保存内容。登录账号后，点击页脚的 **“绑定本机截图客户端”**，将正在设备空间运行的客户端切换到此账号。已有账号绑定的客户端也可退出后使用以下命令登录：

```bash
uv run --no-sync screenbridge desktop --login
```

此命令交互输入账号密码，不需把密码放在命令行参数中。原生客户端的会话文件 `.runtime/desktop-session.txt` 不要分享。

账号同步依赖设备可访问同一台服务，不包括托管云账号、短信登录或跨网穿透。默认局域网 HTTP 适用于本机与可信局域网；跨网络部署需配置 HTTPS/WSS 反向代理，避免将 8765 端口直接暴露到公网。手机 HTTP 页面可能不能自动复制，可长按文本手动复制。

## 命令行

```bash
uv sync --frozen --extra desktop
# 真实模式可以先打开工作台，再配置模型 Key；无 Key 的模型会拒绝提交。
uv run --no-sync screenbridge serve --open
# 或明确选择演示：不识别题图，不调用真实 AI。
uv run --no-sync screenbridge serve --demo --open
# 另一个终端，启动原生截图客户端。
uv run --no-sync screenbridge desktop
# 第二块屏幕，指定快捷键与固定风格。
uv run --no-sync screenbridge desktop --monitor 2 --hotkey '<ctrl>+<shift>+x' --mode code
uv run --no-sync screenbridge serve --phone-host 192.168.1.10
```

桌面客户端未指定 `--mode` 时，使用工作台当前风格、模型选择和提示词。服务器最多允许每个空间 5 个待处理任务，全局最多 20 个任务和 3 个并行 AI 调用。图片限 8 MB / 2400 万像素，最长边最多保留 4096 像素，使用高质量 JPEG，并请求模型以 `detail: high` 分析。小字题图宜使用区域截图，保留完整题干与选项；多模型与综合建议会产生多次 API 调用。

服务端支持 Linux。原生截图依赖图形桌面：Windows 为主要目标；macOS 需屏幕录制及辅助功能权限，Linux 通常需 X11。本次 Linux 云环境没有图形桌面，因此 Windows 全局快捷键、原生选框、右键长按与多显示器需要在 Windows 实测。

## OpenAI 与兼容服务

API Key 在 [OpenAI 开发者平台](https://platform.openai.com/api-keys) 点击 **Create new secret key** 创建，只在软件模型配置里填写。API 计费与 ChatGPT 会员分开；忘记完整 Key 时需创建新 Key。

OpenAI 官方 SDK 当前列出的型号包含 `gpt-6-astra` 与 `gpt-6.1-sol`；不要把它们合写成 `gpt-6.1-astra`。在“模型名称”填写账号实际可用的模型 ID，再测试连接并上传题图验证图片能力。软件对 OpenAI 官方接口使用 `max_completion_tokens`，并为 GPT-5/6 推理留出输出预算；兼容服务仍保留其常见的 `max_tokens` 参数。模型可用性、图片能力与实际质量需要你的 Key 验证，SDK 中列出型号并不代表每个账号均有权限。

点击 **更换模型 · Astra / Sol** 可从预设切换实际模型 ID；也可点击 **读取账号可用模型**，从当前 Key 的 `/models` 返回列表中选择。此列表不验证图片能力，Key 需要读取模型列表权限。预设不代表账号已拥有权限，模型不可用时会提示核对 ID 和项目权限，不会自动回退到 `gpt-4.1-mini`。**保存并应用** 默认将工作台切换为当前模型；多模型对照时再勾选其他配置。任务结果显示请求模型与接口实际返回的模型 ID（若不同）。

Astra/Sol 预设默认使用 **高** 推理强度和 **16384** 推理与输出总预算，可在配置里调整。强度与总预算只用于 OpenAI 推理模型；兼容服务和非推理模型不会收到推理参数。不支持指定强度时选择“自动”。较高强度和预算可能增加耗时、费用，也可能受账号每分钟 token 限额约束。速答/详细解析控制呈现风格，与推理强度分别设置。输出被预算截断时会报错，避免把不完整答案展示为完成。

分析提示要求保留全部条件、检查否定词与公式、核验关键依据；看不清或缺失关键条件时应明确说明。遇到识别偏差，先使用 OCR 核对题干，在“题干 / 补充条件”修正后重新分析。上述改动改善输入与推理设置，不证明真实题目的正确率；具体效果需要用清晰题图和标准答案检验。

```dotenv
INTERVIEW_AI_KEY=你的密钥
INTERVIEW_AI_BASE_URL=https://api.openai.com/v1
INTERVIEW_AI_MODEL=gpt-4.1-mini
```

也可在工作台保存不同的视觉模型配置。编辑配置时 Key 留空会保留原 Key。连接测试发送一个短文本请求，验证认证与模型访问；图片能力仍需上传题图验证。演示模式的连接测试明确标注为模拟响应。

原图、缩略图、复盘和模型配置存放在 `.runtime/workspace.sqlite3`；其中模型密钥是服务器本地保存的值，数据库没有应用层加密，不要分享 `.runtime`。`.env`、会话、数据库与二维码均已被 Git 忽略。默认每次服务启动更新设备配对令牌，账号会话可跨重启使用；设置至少 24 字符的 `INTERVIEW_PAIR_TOKEN` 可固定设备配对。

## API 返回 429 时

先看软件显示的错误类型：

- **insufficient_quota / billing**：API 额度不足、计费未启用或计费限额已达到。检查 [API Billing](https://platform.openai.com/settings/organization/billing/overview) 的余额与计费状态；ChatGPT 会员不包含 API 调用额度。此类错误不自动重试。
- **rate_limit_exceeded**：请求频率或每分钟 token 限额达到。软件按服务返回的等待时间最多自动重试 2 次；需要等待超过 20 秒时会提示手动等待。先选一个模型、关闭综合建议，并检查 [Limits](https://platform.openai.com/settings/organization/limits)。
- **单次请求超过限额**：需要缩小输入或调整模型的请求预算、使用限额；等待不改变单次请求大小。
- **未提供可识别代码**：无法仅凭 429 判断原因，不会盲目重试；分别检查 Billing 和 Limits。

升级软件只能改进错误提示与限流处理，不能增加 API 账户余额或权限。充值或开通 API 计费后，再测试连接并上传题图验证。密钥只在软件内填写，不要发送到聊天或分享数据文件夹。

## 开发与验证

Windows 开发机可运行 `uv run --no-sync screenbridge-app` 打开桌面启动窗口。构建应用包：

```bash
uv sync --frozen --extra desktop --extra dev --extra build
uv run --no-sync pyinstaller --noconfirm --clean ScreenBridge.spec
```

成品位于 `dist/ScreenBridge/`。Windows 打包工作流会直接执行打包后的 exe，验证自带界面资源、服务启动、原生截图客户端及截图上传流程后，才上传下载包；此检查使用演示模式，不证明真实 AI 识别效果。

```bash
uv sync --frozen --extra dev
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv build
```

在此云环境运行 uv 前设置 `UV_CACHE_DIR=/workspace/.cache/uv`；Linux 安装 desktop 依赖时使用 `CC=gcc CXX=g++`。

已提供后端与截图逻辑测试，涵盖模型配置密钥脱敏、并行分析、部分失败、协同建议、OCR、账号隔离与注销、重启持久化、题图访问控制、原生截图命令、工作台设置和选框坐标。GitHub Actions 在 Linux 与 Windows 上运行相关测试；这些测试不替代真实 Windows 桌面交互测试。

对正在运行的演示服务，可用本环境的 Chromium 执行浏览器检查：

```bash
uv run --no-sync python scripts/smoke_browser.py --chromium /usr/bin/chromium
```

该检查使用独立的电脑与手机浏览器上下文，验证注册登录、模型配置、连接测试、裁剪、双模型对照、综合建议、OCR、跨设备同步、刷新恢复及手机布局，并保存无密钥的截图到 `.runtime/`。演示使用的模型返回是模拟数据，不能证明真实 OCR 或解题质量。

`/healthz` 只表示服务启动。真实 AI 验证需使用有效 Key 上传题图，并检查完成后的题干与答案。
