# -*- mode: python ; coding: utf-8 -*-
"""保卫鱼娘挂机助手 · PyInstaller 打包配置（单文件 exe，含 Playwright driver）"""
import os
import playwright

IS_DEBUG_BUILD = os.environ.get("GUARD_FISH_BUILD_FLAVOR") == "debug"
ENTRY = "yu_debug.py" if IS_DEBUG_BUILD else "yu_gui.py"
EXE_NAME = "保卫鱼娘助手-Debug" if IS_DEBUG_BUILD else "保卫鱼娘助手"
PW_DIR = os.path.dirname(playwright.__file__)
DRIVER = os.path.join(PW_DIR, "driver")          # node.exe + package/cli.js（约 100MB）

datas = [(DRIVER, os.path.join("playwright", "driver")), ("VERSION", "."),
         ("api_baseline.json", ".")]
hiddenimports = ["playwright", "playwright.sync_api", "playwright.async_api", "greenlet"]

a = Analysis(
    [ENTRY],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "PIL", "scipy", "pytest",
              "IPython", "notebook", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=EXE_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,                 # 纯 GUI，不弹黑色控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
