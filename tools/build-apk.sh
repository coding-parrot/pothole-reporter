#!/usr/bin/env bash
# Build the APK and refuse to hand back one that does not contain the current source.
#
# Gradle packages android/app/src/main/assets/public, which is a COPY made by
# "cap copy". Editing static/ or www/ and running gradle alone produces a stale APK
# that looks fine and is missing the change you just made. That has shipped once.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
APK=android-app/android/app/build/outputs/apk/debug/app-debug.apk
FORBIDDEN_STATE_ASSETS=(
  delhi-coverage.json
  karnataka-bodies.json
  kolkata-coverage.json
  maharashtra-coverage.json
  tenders.json
)

echo "1/5 mirroring static/ into www/"
for f in standalone.js index.html pack-manifest.json pack-manifest-v1.26.json pack-manifest-v1.27.json pack-manifest-v1.28.json pack-manifest-v1.29.json pack-manifest-v1.30.json pack-manifest-v1.31.json highway-manifest.json; do cp "static/$f" "android-app/www/$f"; done

for asset in "${FORBIDDEN_STATE_ASSETS[@]}"; do
  [ ! -e "static/$asset" ] || { echo "FAIL: state data must not be bundled in static/: $asset"; exit 1; }
  [ ! -e "android-app/www/$asset" ] || { echo "FAIL: state data must not be bundled in Android www/: $asset"; exit 1; }
done

echo "2/5 validating hosted data packs"
python3 tools/build-state-packs.py --check
python3 tools/build-national-highways.py --check

echo "3/5 syncing www into the android assets gradle actually packages"
(cd android-app && npx cap copy android >/dev/null)

echo "4/5 building"
rm -f "$APK"
(cd android-app/android && ./gradlew --offline assembleDebug -q)
[ -f "$APK" ] || { echo "FAIL: gradle produced no APK"; exit 1; }

echo "5/5 verifying the APK contains this source"
fail=0
same() {  # a file inside the APK must be byte-identical to the source
  if diff -q <(unzip -p "$APK" "assets/public/$1") "android-app/www/$1" >/dev/null; then
    echo "  ok   $1 matches source"
  else
    echo "  FAIL $1 in the APK differs from android-app/www/$1"; fail=1
  fi
}
same standalone.js
same index.html
same pack-manifest.json
same pack-manifest-v1.26.json
same pack-manifest-v1.27.json
same pack-manifest-v1.28.json
same pack-manifest-v1.29.json
same pack-manifest-v1.30.json
same pack-manifest-v1.31.json
same highway-manifest.json

for asset in "${FORBIDDEN_STATE_ASSETS[@]}"; do
  if unzip -Z1 "$APK" | grep -Fx "assets/public/$asset" >/dev/null; then
    echo "  FAIL state data is bundled in the APK: $asset"; fail=1
  else
    echo "  ok   $asset is not bundled"
  fi
done

if unzip -Z1 "$APK" | grep -Eq '^assets/public/packs/v1/highways/'; then
  echo "  FAIL National Highway geometry tiles are bundled in the APK"; fail=1
else
  echo "  ok   National Highway geometry tiles are not bundled"
fi

n=$(unzip -p "$APK" assets/public/standalone.js | grep -c 'sk-proj\|sk-[A-Za-z0-9]\{20\}' || true)
[ "$n" = "0" ] && echo "  ok   no API key baked in" || { echo "  FAIL an API key is in the APK"; fail=1; }

[ "$fail" = "0" ] || { echo "APK REJECTED"; exit 1; }
printf "APK OK  %.1f MB  %s\n" "$(echo "scale=2; $(stat -f%z "$APK")/1048576" | bc)" "$ROOT/$APK"
