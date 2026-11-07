@echo off
chcp 65001 >nul
title 日淘比价助手 - 本地服务
cd /d "%~dp0"
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo 没找到可用的 Python 3.10 以上，请先安装并勾选 Add to PATH
  pause
  exit /b 1
)
echo 正在启动本地比价服务...
echo 电脑用 http://127.0.0.1:8787/ ，手机用窗口里显示的局域网 IP
echo 保持这个窗口开着，关掉就等于停止服务。
echo.
rem 延时 3 秒再开浏览器：服务启动前访问会报「127.0.0.1 拒绝连接」
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8787/"
python app\server.py
echo.
echo 服务已停止或启动失败。若上方有报错，请按报错排查；否则是窗口被关闭。
pause
