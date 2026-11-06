#!/usr/bin/env bash
# Manual APK build -- no Gradle / Android Studio required.
# Uses the SDK packages in ../.tools and the JDK bundled with Android Studio.
# All tool paths are relative to the workspace root because aapt2/javac are
# native Windows binaries and cannot understand Git Bash's /d/... paths.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BT_REL=".tools/bt/android-14"
AJAR_REL=".tools/plat/android-35/android.jar"
JBR="${JBR:-/d/AndroidStudio/jbr}"
OUT=".build"

[ -f "$BT_REL/aapt2.exe" ] || { echo "缺少 build-tools: $BT_REL"; exit 1; }
[ -f "$AJAR_REL" ] || { echo "缺少 android.jar: $AJAR_REL"; exit 1; }
[ -f "$JBR/bin/javac.exe" ] || { echo "缺少 JDK: $JBR"; exit 1; }

export JAVA_HOME="$JBR"
export PATH="$JBR/bin:$PATH"
W_AJAR="$(cygpath -w "$AJAR_REL")"
rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/gen" "$OUT/dex"

echo "[1/7] 编译资源"
"$BT_REL/aapt2.exe" compile --dir android/app/src/main/res -o "$OUT/res.zip"
echo "[2/7] 链接 APK 骨架"
"$BT_REL/aapt2.exe" link -o "$OUT/base.apk" -I "$W_AJAR" \
  --manifest android/app/src/main/AndroidManifest.xml \
  -R "$OUT/res.zip" --auto-add-overlay --java "$OUT/gen" \
  --min-sdk-version 26 --target-sdk-version 34
echo "[3/7] 编译 Java"
find android/app/src/main/java "$OUT/gen" -name "*.java" > "$OUT/sources.txt"
"$JBR/bin/javac.exe" --release 8 -encoding UTF-8 -classpath "$W_AJAR" \
  -d "$OUT/classes" @"$OUT/sources.txt"
echo "[4/7] 转 DEX"
# Bundle classes into a jar first; the R8 shipped in build-tools r34 crashes on
# anonymous inner classes, so prefer a newer R8 when available.
"$JBR/bin/jar.exe" cf "$OUT/classes.jar" -C "$OUT/classes" .
if [ -f ".tools/r8.jar" ]; then
  "$JBR/bin/java.exe" -cp "$(cygpath -w .tools/r8.jar)" com.android.tools.r8.D8 \
    --lib "$W_AJAR" --min-api 26 \
    --output "$(cygpath -w "$OUT/dex")" "$(cygpath -w "$OUT/classes.jar")"
else
  "$BT_REL/d8.bat" --lib "$W_AJAR" --min-api 26 \
    --output "$OUT/dex" "$OUT/classes.jar"
fi
echo "[5/7] 打包 classes.dex"
python - "$OUT/base.apk" "$OUT/dex/classes.dex" <<'PY'
import sys, zipfile, shutil
apk, dex = sys.argv[1], sys.argv[2]
tmp = apk + ".tmp"
with zipfile.ZipFile(apk, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        if item.filename != "classes.dex":
            zout.writestr(item, zin.read(item.filename))
    zout.write(dex, "classes.dex")
shutil.move(tmp, apk)
PY
echo "[6/7] 对齐"
"$BT_REL/zipalign.exe" -f 4 "$OUT/base.apk" "$OUT/aligned.apk"
# Keep the signing key outside .build so rebuilt APKs stay signature-compatible
# and can be installed over the previous version without uninstalling.
KS="android/debug.keystore"
if [ ! -f "$KS" ]; then
  "$JBR/bin/keytool.exe" -genkeypair -keystore "$KS" \
    -storepass android -keypass android -alias androiddebugkey \
    -dname "CN=Android Debug,O=Android,C=US" -keyalg RSA -keysize 2048 \
    -validity 10000 >/dev/null 2>&1
fi
echo "[7/7] 签名"
"$BT_REL/apksigner.bat" sign --ks "$KS" \
  --ks-pass pass:android --key-pass pass:android \
  --out "RitaoCompare-debug.apk" "$OUT/aligned.apk"
"$BT_REL/apksigner.bat" verify "RitaoCompare-debug.apk"
ls -lh "RitaoCompare-debug.apk"
echo "完成"
