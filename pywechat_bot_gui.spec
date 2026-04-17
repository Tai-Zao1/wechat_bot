# -*- mode: python ; coding: utf-8 -*-


block_cipher = None


hiddenimports = [
    "client_api",
    "client_api.client",
    "wechat_bot.check_wechat_status",
    "wechat_bot.open_wechat_window",
    "wechat_bot.auto_reply_unread",
    "wechat_bot.add_friend_by_phone",
    "wechat_bot.local_bailian",
    "wechat_bot.scripts",
    "wechat_bot.scripts.registry",
    "wechat_bot.scripts.check_wechat",
    "wechat_bot.scripts.open_wechat",
    "wechat_bot.scripts.auto_reply",
    "wechat_bot.scripts.add_friends",
]


a = Analysis(
    ["wechat_bot/pyqt_app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pywechat"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pywechat_bot_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="pywechat_bot_gui",
)
