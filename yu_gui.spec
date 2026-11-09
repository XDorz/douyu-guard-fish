# -*- mode: python ; coding: utf-8 -*-
"""保卫鱼娘挂机助手 · 单文件 EXE，含 Patchright driver。"""
import os
import patchright

IS_DEBUG_BUILD = os.environ.get("GUARD_FISH_BUILD_FLAVOR") == "debug"
ENTRY = "yu_debug.py" if IS_DEBUG_BUILD else "yu_gui.py"
EXE_NAME = "保卫鱼娘助手-Debug" if IS_DEBUG_BUILD else "保卫鱼娘助手"
PW_DIR = os.path.dirname(patchright.__file__)
DRIVER = os.path.join(PW_DIR, "driver")          # node.exe + package/cli.js（约 100MB）

datas = [(DRIVER, os.path.join("patchright", "driver")), ("VERSION", "."),
         ("api_baseline.json", ".")]
hiddenimports = ["patchright", "patchright.sync_api", "patchright.async_api", "greenlet"]

a = Analysis(
    [ENTRY],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright", "matplotlib", "numpy", "pandas", "PIL", "scipy", "pytest",
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
