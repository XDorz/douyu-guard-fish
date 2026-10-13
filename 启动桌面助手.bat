@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先跑打包好的 exe；没有就用本机 Python 跑源码
rem （tkinter 需完整版 Python；若用 embeddable/精简版 Python 则无 tkinter）
if exist "保卫鱼娘自动挂机\保卫鱼娘助手.exe" (
    start "" "%~dp0保卫鱼娘自动挂机\保卫鱼娘助手.exe"
    exit /b
)

set PY=python
where python >nul 2>nul || set PY=py
"%PY%" yu_gui.py
if errorlevel 1 pause
