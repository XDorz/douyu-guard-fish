# -*- mode: python ; coding: utf-8 -*-
"""API 监听实验版：只打包浏览器和监听器，不包含挂机 GUI。"""
import os
import patchright

PW_DIR = os.path.dirname(patchright.__file__)
DRIVER = os.path.join(PW_DIR, "driver")

datas = [(DRIVER, os.path.join("patchright", "driver")),
         ("VERSION", "."), ("API_LAB_VERSION", ".")]
hiddenimports = ["patchright", "patchright.sync_api", "patchright.async_api", "greenlet"]

a = Analysis(
    ["api_lab_gui.py"],
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
    pyz, a.scripts, a.binaries, a.datas,
    name="保卫鱼娘API监听实验版",
    debug=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
