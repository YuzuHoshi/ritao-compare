@echo off
chcp 65001 >nul
title 日淘比价助手 - 本地服务
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo 没找到 python，请先安装 Python 3.10 以上并勾选 Add to PATH
  pause
  exit /b 1
)
echo 正在启动本地比价服务...
echo 浏览器会显示两个地址：电脑用 127.0.0.1，手机用局域网 IP
echo 保持这个窗口开着，关掉就等于停止服务。
echo.
start "" "http://127.0.0.1:8787/"
python app\server.py
pause
