#!/usr/bin/env bash
# Linux / CI APK build -- no Gradle, no Android Studio, no Windows tools.
# Used by .github/workflows/release.yml on ubuntu-latest, which ships with
# an Android SDK and a JDK. Call from the repo root:
#
#   bash android/build-linux.sh dist/RitaoCompare-v1.2.0.apk
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT_APK="${1:-RitaoCompare.apk}"
OUT=".build-linux"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/usr/local/lib/android/sdk}}"
BT="$(find "$SDK/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)"
AJAR="$(find "$SDK/platforms" -mindepth 1 -maxdepth 1 -type d -name 'android-*' \
        | sort -V | tail -1)/android.jar"
[ -x "$BT/aapt2" ] || { echo "aapt2 not found under $SDK"; exit 1; }
[ -f "$AJAR" ] || { echo "android.jar not found under $SDK"; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/gen" "$OUT/dex"
mkdir -p "$(dirname "$OUT_APK")"

echo "[1/6] compile resources"
"$BT/aapt2" compile --dir android/app/src/main/res -o "$OUT/res.zip"

echo "[2/6] link APK skeleton"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$AJAR" \
  --manifest android/app/src/main/AndroidManifest.xml \
  -R "$OUT/res.zip" --auto-add-overlay --java "$OUT/gen" \
  --min-sdk-version 26 --target-sdk-version 34

echo "[3/6] compile Java"
find android/app/src/main/java "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac --release 8 -encoding UTF-8 -nowarn -classpath "$AJAR" \
  -d "$OUT/classes" @"$OUT/sources.txt"
jar cf "$OUT/classes.jar" -C "$OUT/classes" .

echo "[4/6] dex + inject classes.dex"
"$BT/d8" --lib "$AJAR" --min-api 26 --output "$OUT/dex" "$OUT/classes.jar"
python3 - "$OUT/base.apk" "$OUT/dex/classes.dex" <<'PY'
import shutil, sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
tmp = apk + ".tmp"
with zipfile.ZipFile(apk) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        if item.filename != "classes.dex":
            zout.writestr(item, zin.read(item.filename))
    zout.write(dex, "classes.dex")
shutil.move(tmp, apk)
PY

echo "[5/6] align"
"$BT/zipalign" -f 4 "$OUT/base.apk" "$OUT/aligned.apk"

echo "[6/6] sign with a throwaway debug key"
keytool -genkeypair -keystore "$OUT/debug.keystore" \
  -storepass android -keypass android -alias androiddebugkey \
  -dname "CN=Android Debug,O=Android,C=US" -keyalg RSA -keysize 2048 \
  -validity 10000 >/dev/null 2>&1
"$BT/apksigner" sign --ks "$OUT/debug.keystore" \
  --ks-pass pass:android --key-pass pass:android \
  --out "$OUT_APK" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT_APK"
ls -lh "$OUT_APK"
echo "done"
