@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Douyu Guard Fish Auto Bot
echo   Ctrl+C 停止
echo ============================================
rem 优先用打包好的 exe
if exist "保卫鱼娘自动挂机\保卫鱼娘助手.exe" (
    "%~dp0保卫鱼娘自动挂机\保卫鱼娘助手.exe" %*
    pause
    exit /b
)
rem 回退到源码运行（需已安装 Python + playwright）
python bot.py %*
pause
