#!/usr/bin/env bash
# Install the app on a running Android emulator or device, drive first run, and fail on
# any JavaScript error.
#
#   tools/harness/emulator-smoke.sh [--keep]
#
# The browser suites run the same bundle in a desktop engine. This runs it where testers
# run it: Android's WebView, inside the packaged APK, from a cleared install. v1.38.1
# shipped a build whose whole script died at load, and nothing in CI would have noticed
# because nothing launched the app.
set -euo pipefail
cd "$(dirname "$0")/../.."

SDK="${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}"
ADB="$SDK/platform-tools/adb"
PACKAGE=com.gauravsen.potholereporter
APK=android-app/android/app/build/outputs/apk/debug/app-debug.apk
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

[ -x "$ADB" ] || { echo "FAIL adb not found at $ADB"; exit 2; }
if [ -z "$("$ADB" devices | awk 'NR>1 && $2=="device"')" ]; then
  echo "SKIP no Android device or emulator is attached"
  exit 0
fi

echo "1/5 building the debug APK"
(cd android-app && npx cap copy android >/dev/null)
(cd android-app/android && ./gradlew assembleDebug -q)
[ -s "$APK" ] || { echo "FAIL gradle produced no APK"; exit 1; }

echo "2/5 installing and clearing app data"
"$ADB" install -r -d "$APK" >/dev/null
"$ADB" shell pm clear "$PACKAGE" >/dev/null
"$ADB" logcat -c 2>/dev/null || true

echo "3/5 launching"
"$ADB" shell am start -n "$PACKAGE/.MainActivity" >/dev/null
# The WebView needs a moment on a cold start; poll rather than guess.
# mCurrentFocus can belong to a systemui ANR dialog on a loaded emulator while our
# activity is perfectly healthy underneath. mFocusedApp is the app the window manager
# considers foreground, so accept either.
for _ in $(seq 1 30); do
  sleep 2
  focus="$("$ADB" shell dumpsys window 2>/dev/null \
    | grep -cE "(mCurrentFocus|mFocusedApp).*$PACKAGE" || true)"
  [ "$focus" != "0" ] && break
done
[ "$focus" != "0" ] || { echo "FAIL the app never took focus"; exit 1; }

echo "4/5 driving first run: Settings, Continue, Home"
sleep 6
for _ in 1 2 3 4 5 6; do "$ADB" shell input swipe 540 1800 540 400 200; sleep 0.3; done
# The green Continue button sits at the bottom of the mandatory first-run Settings.
"$ADB" shell input tap 540 2148
sleep 6

echo "5/5 checking for JavaScript errors"
errors="$("$ADB" logcat -d 2>/dev/null \
  | grep -iE "Uncaught|is not defined|ReferenceError|TypeError|SyntaxError" \
  | grep -viE "AppsFilter|PreferenceController|BaseSearchIndex|Phenotype" || true)"
if [ -n "$errors" ]; then
  echo "FAIL the app reported JavaScript errors on first run:"
  echo "$errors" | head -10
  exit 1
fi

screen="$("$ADB" shell dumpsys window 2>/dev/null \
  | grep -cE "(mCurrentFocus|mFocusedApp).*$PACKAGE" || true)"
[ "$screen" != "0" ] || { echo "FAIL the app left the foreground during first run"; exit 1; }

[ "$KEEP" = "1" ] || "$ADB" shell am force-stop "$PACKAGE" >/dev/null
echo "emulator smoke passed: fresh install reached Home with no JavaScript errors"
