@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 打开浏览器手动登录斗鱼（登录态保存在本机 chrome_profile，可直接复用）

rem 优先用打包好的 exe
if exist "保卫鱼娘自动挂机\保卫鱼娘助手.exe" (
    "%~dp0保卫鱼娘自动挂机\保卫鱼娘助手.exe" --login
    pause
    exit /b
)

set PY=python
where python >nul 2>nul || set PY=py
"%PY%" bot.py --login
pause
