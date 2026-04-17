# WeChat Bot / pywechat （AI润色）

这是一个基于 Windows UI Automation 与 `pywinauto` 的 PC 微信自动化项目，包含面向微信 4.1.8 封装的自动化能力以及 `wechat_bot` GUI 工具。项目不涉及逆向 Hook，主要通过可访问性控件树、键鼠操作和窗口控件识别完成自动化流程。

本仓库适用于 Windows 环境下的微信自动化开发、自动回复、好友添加、好友资料同步、消息监听、文件/朋友圈等 UI 自动化流程。

## 如果你第一次看这个仓库

先不用从几千行的自动化代码开始翻。按这个顺序看最省时间：

1. `README.md`
   先知道项目分几层、入口在哪、支持什么环境。
2. `wechat_bot/pyqt_app.py`
   这是 GUI 主入口，大部分功能都是从这里触发的。
3. `wechat_bot/friend_messaging_service.py`
   这是好友列表、头像同步、定时群发的业务层。
4. `wechat_bot/auto_reply_unread.py`
   这是自动回复主流程。
5. `client_api/client.py`
   如果你接后端接口，这里是唯一需要重点看的 API 客户端。
6. `pyweixin/WeChatTools.py` + `pyweixin/WeChatAuto.py`
   如果你要改底层 UI 自动化，再进入这里。

## 三层结构

这个项目可以简单理解为三层：

- `wechat_bot/`
  面向使用者的业务层。GUI、自动回复、批量加好友、好友同步都在这里。
- `client_api/`
  面向后端接口的网络层。只负责登录、聊天接口、好友同步、在线检测等 HTTP 请求。
- `pyweixin/`
  面向 PC 微信 4.1.8 的 UI 自动化底层。只负责“怎么点微信、怎么找控件、怎么拿数据”。

这样看代码时，不容易混：

- 想改界面、运行流程、业务规则，看 `wechat_bot/`
- 想改接口地址、鉴权、请求参数，看 `client_api/`
- 想修 UIA 控件定位、聊天窗口、通讯录、头像抓取，看 `pyweixin/`

## 重要声明

请勿将本项目用于任何非法商业活动、侵犯隐私、骚扰、欺诈、批量营销、绕过平台规则或其他违法违规用途。因此造成的一切后果由使用者自行承担。

本项目仅面向 PC 微信 4.1.8。其他微信版本的 UI 结构、控件名称和菜单行为可能不同，均不在当前支持范围内。涉及真实账号和业务数据时，请务必先在测试账号和测试环境验证。

## 致谢

本项目基于并延续了原作者 Hello-Mr-Crab 的 `pywechat` 项目思路与代码基础：

https://github.com/Hello-Mr-Crab/pywechat/

感谢原作者对 PC 微信 UI 自动化能力的探索与开源贡献。

## 支持环境

- 操作系统：Windows 10 / Windows 11。
- 微信版本：仅支持 PC 微信 4.1.8。
- Python：建议 Python 3.10+。
- UI 前提：已登录 PC 微信，并保持微信主窗口可被系统 UI Automation 访问。

## 项目结构

```text
.
├── wechat_bot/              # 业务层：GUI、自动回复、批量加好友、好友同步
│   ├── pyqt_app.py          # GUI 主入口
│   ├── auto_reply_unread.py # 自动回复主流程
│   ├── friend_messaging_service.py # 好友列表、头像同步、定时群发
│   ├── add_friend_by_phone.py # 批量加好友
│   ├── local_bailian.py     # 本地阿里百炼调用
│   ├── common/              # 默认值、JSON存储、自动回复公共逻辑
│   └── core/                # 路径、类型、全局配置
├── client_api/              # 网络层：后端接口客户端
│   └── client.py            # 登录、聊天、好友同步等 HTTP 封装
├── pyweixin/                # 底层自动化：PC 微信 4.1.8 UI 自动化
│   ├── WeChatTools.py       # 导航、窗口、基础定位
│   ├── WeChatAuto.py        # 消息、通讯录、文件、朋友圈等能力
│   └── Uielements.py        # UI 控件定位参数
├── pywechat/                # 原项目保留目录，当前不作为支持入口
├── installer/               # NSIS 安装包配置
├── scripts/                 # 打包脚本
├── pics/                    # 文档图片资源
├── inspcet/                 # Windows UI Inspect 工具
├── .env.example             # 环境变量模板
└── pywechat_bot_gui.spec    # PyInstaller 固定打包配置
```

注意：不要再使用气泡左右几何位置判断是否为己方消息。微信 4.1.8 的 UI 结构下该方式不稳定，容易误判。

## 快速开始

创建并激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
pip install -r requirements.txt
pip install -r requirements-gui.txt
```

以可编辑模式安装：

```powershell
pip install -e .
```

运行 GUI：

```powershell
python .\wechat_bot\pyqt_app.py
```

最小导入检查：

```powershell
python -c "from pyweixin import Navigator"
```

说明：当前支持入口为 `wechat_bot` 与 `pyweixin`，仅按 PC 微信 4.1.8 验证。

## 常见改动去哪里

如果你是二次开发，通常按下面找文件就够了：

- 改 GUI 按钮、页面、日志展示：`wechat_bot/pyqt_app.py`
- 改自动回复逻辑、聊天模式、短链规则：`wechat_bot/auto_reply_unread.py`
- 改好友列表加载、头像同步、定时群发：`wechat_bot/friend_messaging_service.py`
- 改批量加好友、Excel 导入、API 拉号：`wechat_bot/add_friend_by_phone.py`
- 改后端接口、登录、token、请求参数：`client_api/client.py`
- 改微信窗口定位、通讯录、聊天窗口、会话列表：`pyweixin/WeChatTools.py`
- 改消息、通讯录、文件、朋友圈等底层自动化能力：`pyweixin/WeChatAuto.py`
- 改控件定位参数：`pyweixin/Uielements.py`

## 运行流转

核心链路可以这样理解：

1. GUI 从 `wechat_bot/pyqt_app.py` 启动。
2. GUI 根据按钮动作启动对应业务脚本。
3. 业务脚本按需要调用：
   `client_api/` 访问后端接口，或调用 `pyweixin/` 操作 PC 微信。
4. 公共配置、路径、类型由 `wechat_bot/common/` 和 `wechat_bot/core/` 提供。

## 接口配置

本项目支持两种运行模式：

- API 模式：登录、聊天回复、好友同步走后端接口。
- 本地模式：不登录后端，聊天回复走本地阿里百炼应用，好友列表只保存到本机缓存。

运行前复制环境变量模板：

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
```

配置示例：

```env
PYWECHAT_ENV=prod
PYWECHAT_API_BASE_URL=https://example.com/api
PYWECHAT_CHECK_ONLINE_BASE_URL=https://example.com/api
PYWECHAT_CHAT_MODE=api
PYWECHAT_BAILIAN_APP_ID=
PYWECHAT_BAILIAN_API_KEY=
```

`.env` 只用于本机配置，不要提交到 Git。仓库已通过 `.gitignore` 忽略 `.env`、日志、缓存、虚拟环境和构建产物。

API 日志会自动脱敏 `token`、`Authorization`、`password`、`device_id`、`secret` 等字段。仍建议不要在 issue、PR 或截图中公开账号、手机号、微信号、聊天内容、接口地址和日志原文。

### API 模式

API 模式需要在启动 GUI 后使用后端账号登录。自动回复调用后端 `/autoWx/chat`，好友列表加载完成后会同步到后端。

### 本地模式

本地模式在启动 GUI 时选择“本地模式”，填写阿里百炼 `appId` 与 `apiKey` 后进入控制台。该模式下：

- 不调用后端登录接口。
- 自动回复调用本地配置的阿里百炼应用。
- 好友列表只保存到本机缓存，不同步后端。
- 批量加好友可上传本地 Excel 手机号文件；API 获取手机号添加仅在 API 模式可用。

阿里百炼默认 endpoint：

```text
https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion
```

真实 `apiKey` 仅保存在本机配置中，不会写入仓库。启动子进程时也不会把 `apiKey` 打到运行日志。

## GUI 功能

GUI 入口为：

```powershell
python .\wechat_bot\pyqt_app.py
```

主要封装：

- 检查微信登录状态。
- 打开/恢复微信主窗口。
- 自动回复未读消息。
- 通过本地 Excel 或后端接口读取手机号并添加好友。
- 好友资料与头像同步。
- 定时/批量消息相关辅助流程。

API 模式自动回复会调用 `/autoWx/chat`，并在日志中输出 `[AUTO] 聊天接口参数`，用于确认传给后端的业务字段。本地模式自动回复会输出 `[AUTO] 本地百炼参数`。日志不会输出 token/header/apiKey 明文。

## 自动回复注意事项

- 自动回复运行时应避免人工频繁切换微信窗口。
- 微信 UIA 控件树可能因右键菜单、窗口失焦、系统缩放、微信更新等原因短暂不可用。
- 程序会在调用聊天接口前先做目标消息校验，避免将己方消息、系统消息或异常文本误发给后端。
- `Traceback ... File "...", line ...` 等程序异常文本会被视为不可自动回复内容并跳过。
- GUI 启动自动回复时默认带 `--keep-open`，停止自动回复不会主动关闭微信窗口。

## WeChat 4.1.8 UI 说明

微信 4.1.8 的 UI 自动化可见性与系统可访问性能力有关。实践中，先于微信登录前开启 Windows 讲述人并保持一段时间，可能帮助 UI Automation 暴露更多控件。该行为依赖系统与微信版本，不保证长期稳定。

Windows UI Automation 是可访问性 API，设计上需要向屏幕阅读器暴露 UI 元素信息。微信版本变更后，控件树、类名、菜单项、输入框、会话列表结构都可能变化，因此任何 UI 自动化逻辑都应保守处理异常并保留人工验证。

企业微信等产品可能采用不同 UI 策略，本项目不保证可复用。

## pyweixin 模块概览

`pyweixin` 面向 PC 微信 4.1.8：

- `Navigator`：打开微信内部界面、会话、独立聊天窗口等。
- `Tools`：微信路径、运行状态、窗口与 UI 辅助工具。
- `Messages`：消息发送、聊天记录获取、会话导出等。
- `Files`：文件发送与聊天文件导出。
- `Contacts`：通讯录、好友信息、共同群聊等。
- `Moments`：朋友圈获取、发布、互动相关能力。
- `Monitor`：监听聊天窗口消息。
- `AutoReply`：自动回复相关能力。
- `SystemSettings`：自动化过程中需要的 Windows 设置辅助。

示例：

```python
from pyweixin import Navigator, Monitor

dialog = Navigator.open_seperate_dialog_window(
    friend="文件传输助手",
    window_minimize=False,
    close_weixin=False,
)
result = Monitor.listen_on_chat(dialog_window=dialog, duration="30s")
print(result)
```

## 打包 GUI

先清理缓存：

```powershell
Remove-Item -Recurse -Force .\build, .\dist, .\__pycache__, .\wechat_bot\__pycache__ -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue
```

推荐使用固定 spec 打包：

```powershell
python -m PyInstaller --noconfirm --clean .\pywechat_bot_gui.spec
```

也可以手动执行：

```powershell
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name pywechat_bot_gui ^
  --paths . ^
  --hidden-import wechat_bot.check_wechat_status ^
  --hidden-import wechat_bot.open_wechat_window ^
  --hidden-import wechat_bot.auto_reply_unread ^
  --hidden-import wechat_bot.add_friend_by_phone ^
  --hidden-import wechat_bot.local_bailian ^
  --exclude-module pywechat ^
  .\wechat_bot\pyqt_app.py
```

产物：

```text
dist\pywechat_bot_gui\pywechat_bot_gui.exe
```

## 生成安装包

先确认 NSIS 已安装，并可在命令行访问 `makensis.exe`。然后执行：

```powershell
python .\scripts\build_installer.py
```

常用参数：

```powershell
python .\scripts\build_installer.py --skip-pyinstaller
python .\scripts\build_installer.py --makensis "C:\Program Files (x86)\NSIS\makensis.exe"
python .\scripts\build_installer.py --app-version 1.9.9
python .\scripts\build_installer.py --out-name "PyWechatBotInstaller_v1.9.9.exe"
```

安装包配置文件：

```text
installer\build_config.toml
```

## 开发规范

- 使用 4 空格缩进，源码保持 UTF-8。
- 保持既有命名风格，例如 `WeChatAuto.py`、`WeChatTools.py`、`Messages`、`Navigator`。
- 新增公开接口优先保持静态方法风格：`Class.method(...)`。
- 修改公开方法时尽量补充类型注解。
- 不要提交聊天导出数据、账号标识、本机绝对路径、接口 token、`.env` 或日志文件。
- 涉及 UI 行为变更时，请记录验证环境：Windows 版本、微信版本、语言、窗口状态、系统缩放。

## 测试与验证

当前仓库未提供正式 `tests/` 目录。建议至少执行：

```powershell
python -m py_compile client_api\client.py wechat_bot\auto_reply_unread.py wechat_bot\pyqt_app.py
python -c "from pyweixin import Navigator"
```

功能验证应在已登录微信的真实 Windows 环境完成。涉及自动回复、文件发送、好友添加等流程时，先使用测试账号和测试会话。

## 常见问题

### WinError 10061

`WinError 10061` 表示目标地址或端口拒绝连接，通常是后端服务未启动、端口不通、域名解析错误、代理/防火墙拦截或服务未监听对应端口。

可在 Windows 上检查：

```powershell
Test-NetConnection your-api-host.example.com -Port 443
```

### PyInstaller modulegraph / dis.py 报错

如果打包时出现 `IndexError: tuple index out of range`，优先升级 Python 到 `3.10.11+` 或 `3.11.x`，并升级打包工具：

```powershell
pip install -U pyinstaller pyinstaller-hooks-contrib
```

然后清理缓存后重新打包。

## 许可证与责任

请遵守原项目许可证、微信平台规则、当地法律法规与数据合规要求。使用者应自行承担自动化行为、账号风险、数据处理、接口调用和业务后果。

再次强调：禁止将本项目用于任何非法商业活动，因此造成的一切后果由使用者自行承担。
