# Repository Guidelines

## 项目结构与模块组织
- 不要在使用气泡的几何来判断是否自己发送的消息了，4.1的微信不支持了
- `pyweixin/`：面向 WeChat 4.1+ 的主自动化模块（Windows 10/11，优先维护）。
- `pywechat/`：WeChat 3.9 时代的兼容模块（主要是 32 位场景）。
- `pics/`：文档截图与 GIF 资源。
- `inspcet/`：Windows UI 检查工具（`inspect_x86.exe`、`inspect_x64.exe`）。
- 根目录文档：`README.md`、`Weixin4.0.md`、两份中文操作手册（`*.docx`）。
- 打包与配置：`setup.py`、`pyproject.toml`、`setup.cfg`、`requirements.txt`。

## 构建、测试与开发命令
- `python -m venv .venv && source .venv/bin/activate`：创建并激活本地虚拟环境。
- `pip install -r requirements.txt`：安装运行依赖。
- `pip install -e .`：以可编辑模式安装，便于本地开发调试。
- `python -c "from pyweixin import Navigator"`：`pyweixin` 最小导入冒烟检查。
- `python -c "from pywechat import Tools"`：`pywechat` 兼容场景导入检查（仅 32 位相关场景）。

说明：本项目是 Windows UI 自动化项目，功能验证应在已登录微信的真实 Windows 环境完成。

## 代码风格与命名规范
- 统一使用 4 空格缩进，源码文件保持 UTF-8 编码。
- 保持既有命名风格：模块名如 `WeChatAuto.py`、`WeChatTools.py`，类名如 `Messages`、`Navigator`、`SystemSettings`。
- 新增公开接口保持当前静态方法风格：`Class.method(...)`。
- 新增或修改公开方法时优先补充类型注解（`pyweixin` 已在使用）。

## 测试指南
- 当前仓库未提供正式 `tests/` 测试目录。
- 对行为变更补充最小可复现脚本或冒烟示例，重点覆盖消息、文件、朋友圈流程。
- 涉及 UI 的改动，请在 PR 中写明前置条件（操作系统、微信版本、语言、窗口状态）。

## 提交与 PR 规范
- 当前工作区快照缺少 `.git`，无法直接读取历史提交风格；建议使用清晰祈使句提交，例如：`fix: handle empty chat list in monitor`。
- 每次提交尽量聚焦单一模块（`pyweixin`、`pywechat` 或文档），避免无关格式化噪音。
- PR 至少包含：改动目的、影响模块、手工验证步骤、验证环境（Windows + 微信版本）、UI 变更截图或日志片段。

## 安全与配置建议
- 不要提交个人聊天导出数据、账号标识信息或本机绝对路径。
- 机器相关配置放在本地脚本，不要硬编码到库模块中。
