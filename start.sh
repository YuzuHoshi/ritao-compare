#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
echo "启动日淘比价助手本地服务： http://127.0.0.1:8787"
echo "手机访问同一 Wi-Fi 下服务启动时打印的局域网地址。"
echo "按 Ctrl+C 停止。"
python3 app/server.py
