# PyQt 打包说明

## 1. 安装依赖
```powershell
pip install -r .\wechat_bot\requirements-gui.txt
```

## 2. 本地运行
先复制环境变量模板并填写实际接口地址：
```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
```

`.env` 只放本机配置和接口地址，不要提交到 Git。

```powershell
python .\wechat_bot\pyqt_app.py
```

## 3. 打包（Windows）
先清理缓存（很重要）：
```powershell
Remove-Item -Recurse -Force .\build, .\dist, .\__pycache__, .\wechat_bot\__pycache__ -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue
```

再执行打包：
```powershell
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name pywechat_bot_gui ^
  --paths . ^
  --hidden-import wechat_bot.check_wechat_status ^
  --hidden-import wechat_bot.open_wechat_window ^
  --hidden-import wechat_bot.auto_reply_unread ^
  --hidden-import wechat_bot.add_friend_by_phone ^
  --exclude-module pywechat ^
  .\wechat_bot\pyqt_app.py
```

或直接使用固定 spec（推荐）：
```powershell
python -m PyInstaller --noconfirm --clean .\pywechat_bot_gui.spec
```

产物目录：`dist\pywechat_bot_gui\`
主程序：`dist\pywechat_bot_gui\pywechat_bot_gui.exe`

## 3.1 生成 NSIS 安装包（可选）
先修改配置文件：
```powershell
notepad .\installer\build_config.toml
```

安装 NSIS 后执行：
```powershell
python .\scripts\build_installer.py
```

产物：
- `dist\PyWechatBotInstaller_v版本号.exe`

说明：
- 脚本会先执行 PyInstaller（使用 `pywechat_bot_gui.spec`），再调用 `makensis.exe`。
- 安装器界面已切换为简体中文。
- 默认优先读取配置文件 `installer\build_config.toml`。
- 默认会自动读取项目版本号；当前仓库优先读取 `pyproject.toml`，读不到再回退 `setup.py`。
- 若只想打 NSIS（跳过 PyInstaller）：
```powershell
python .\scripts\build_installer.py --skip-pyinstaller
```
- 若 `makensis.exe` 不在 PATH，可指定路径：
```powershell
python .\scripts\build_installer.py --makensis "C:\Program Files (x86)\NSIS\makensis.exe"
```
- 若要临时覆盖配置文件中的版本号：
```powershell
python .\scripts\build_installer.py --app-version 1.9.9
```
- 若要临时覆盖配置文件中的安装包文件名：
```powershell
python .\scripts\build_installer.py --out-name "PyWechatBotInstaller_客户版_v1.9.9.exe"
```
- 若要临时覆盖配置文件中的安装器显示名称：
```powershell
python .\scripts\build_installer.py --app-name "微信助手客户版"
```
- 若要临时覆盖配置文件中的安装器标题：
```powershell
python .\scripts\build_installer.py --display-name "微信助手客户版 v1.9.9"
```
- 若要临时覆盖配置文件中的安装目录名：
```powershell
python .\scripts\build_installer.py --app-dir-name "WechatBotClient"
```
- 若提示 `makensis.exe not found`：先安装 NSIS（https://nsis.sourceforge.io/Download），安装后重开终端再执行上面的命令。

## 4. 说明
- GUI 按钮是对 `wechat_bot` 下现有脚本的封装调用（打包后通过同一个 exe 的子命令模式执行）：
  - `check_wechat_status.py`
  - `open_wechat_window.py`
  - `auto_reply_unread.py`
  - `add_friend_by_phone.py`
- 运行环境建议 Windows + 已登录微信。
- 首次运行前，请先在 `auto_reply_unread.py` 内填写百炼配置（如需启用）。
- `onedir` 同样会把 Python 运行时和依赖一起打包，目标机器无需单独安装 Python。
- 相比 `onefile`，`onedir` 不需要每次点击按钮都临时解压，稳定性更高。

## 5. 常见报错处理
- 若出现 `IndexError: tuple index out of range`（发生在 `dis.py` / `modulegraph`）：
  - 优先升级打包环境 Python 到 `3.10.11+` 或 `3.11.x`（你当前 `3.10.0` 太旧）。
  - 升级工具：`pip install -U pyinstaller pyinstaller-hooks-contrib`
  - 按上面的“先清理缓存”再重打包。
