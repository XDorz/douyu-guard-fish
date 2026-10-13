@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 打包需完整版 Python（自带 tkinter）；embeddable/精简版 Python 无 tkinter

set PY=python
where python >nul 2>nul || set PY=py

"%PY%" -m pip install --quiet pyinstaller
"%PY%" -m PyInstaller --noconfirm --distpath "保卫鱼娘自动挂机" --workpath "_build_tmp" yu_gui.spec
if exist "_build_tmp" rd /s /q "_build_tmp" 2>nul

echo.
echo 产物：保卫鱼娘自动挂机\保卫鱼娘助手.exe
pause
