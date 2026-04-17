"""pyweixin 是 PC 微信 4.1.8 的 UI 自动化封装层。

这个包是仓库里最底层的自动化能力，主要分为三层：

- `WeChatTools`
  导航、窗口定位、基础 UI 辅助。
- `WeChatAuto`
  消息、通讯录、文件、朋友圈、监控等高层能力。
- `Uielements`
  微信控件定位参数定义，界面变化时通常先看这里。

如果第一次接触这个仓库，建议按这个顺序阅读：

1. `pyweixin/__init__.py`
2. `pyweixin/WeChatTools.py`
3. `pyweixin/WeChatAuto.py`
4. `pyweixin/Uielements.py`

适用环境：

- Windows 10 / 11
- Python 3.10+
- 仅支持 PC 微信 4.1.8
"""
from pyweixin.WeChatAuto import AutoReply,Collections,Call,Contacts,Files,FriendSettings,Messages,Moments,Monitor,Settings
from pyweixin.WeChatTools import Tools,Navigator
from pyweixin.WinSettings import SystemSettings
from pyweixin.Config import GlobalConfig
#@Author:Hello-Mr-Crab,
#@Contributor:Chanpoe,ImViper,clen1,mrhan1993,nmhjklnm,guanjt3
#@version:1.9.8
