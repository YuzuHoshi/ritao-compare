#!/usr/bin/env bash
# Build the companion APK. Easiest: open this folder in Android Studio and use
#   Build > Build Bundle(s) / APK(s) > Build APK(s)
# CLI alternative (requires Gradle 8.6+ and an installed Android SDK):
#   ANDROID_HOME=/path/to/Sdk ./build-apk.sh
set -e
cd "$(dirname "$0")"

if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  echo "未检测到 ANDROID_HOME。请用 Android Studio 打开本目录直接构建，或设置 ANDROID_HOME 后重试。"
  exit 1
fi

if command -v gradle >/dev/null 2>&1; then
  gradle assembleDebug
else
  echo "未找到 gradle 命令。请在 Android Studio 中打开本目录并点 Build APK。"
  exit 1
fi

APK="app/build/outputs/apk/debug/app-debug.apk"
if [ -f "$APK" ]; then
  cp "$APK" ../RitaoCompare-debug.apk
  echo "完成：../RitaoCompare-debug.apk"
fi
