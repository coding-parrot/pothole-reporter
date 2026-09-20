#!/usr/bin/env bash
# Install the app on a running Android emulator or device, drive first run, and fail on
# any JavaScript error.
#
#   tools/harness/emulator-smoke.sh [--keep] [--apk path/to.apk]
#
# The browser suites run the same bundle in a desktop engine. This runs it where testers
# run it: Android's WebView, inside the packaged APK, from a cleared install. v1.38.1
# shipped a build whose whole script died at load, and nothing in CI would have noticed
# because nothing launched the app.
#
# --apk installs that file instead of building the debug APK. Pass the signed release
# APK: R8 only runs there, and 1.39.0's first release build died on the camera
# permission check because R8 had stripped Capacitor's annotations. The debug build,
# which this script used to test, never minifies and never showed it.
#
# After Home it presses Drive and Continue on the camera/location notice, the exact
# taps a tester makes in their first minute, and fails if the process dies.
set -euo pipefail
cd "$(dirname "$0")/../.."

SDK="${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}"
ADB="$SDK/platform-tools/adb"
PACKAGE=dev.aiengg.potholereporter
APK=android-app/android/app/build/outputs/apk/debug/app-debug.apk
KEEP=0
BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --apk) APK=$2; BUILD=0; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done

[ -x "$ADB" ] || { echo "FAIL adb not found at $ADB"; exit 2; }
if [ -z "$("$ADB" devices | awk 'NR>1 && $2=="device"')" ]; then
  echo "SKIP no Android device or emulator is attached"
  exit 0
fi

if [ "$BUILD" = "1" ]; then
  echo "1/6 building the debug APK"
  (cd android-app && npx cap copy android >/dev/null)
  (cd android-app/android && ./gradlew assembleDebug -q)
else
  echo "1/6 using $APK"
fi
[ -s "$APK" ] || { echo "FAIL no APK at $APK"; exit 1; }

echo "2/6 installing from scratch"
# A release APK is signed with a different key than a debug one, so reinstalling over
# it is refused. Always start from no install at all.
"$ADB" uninstall "$PACKAGE" >/dev/null 2>&1 || true
"$ADB" install "$APK" >/dev/null
"$ADB" logcat -c 2>/dev/null || true

echo "3/6 launching"
# The application ID and the Kotlin namespace differ, so "$PACKAGE/.MainActivity"
# does not resolve. Ask the package manager which activity the launcher starts.
LAUNCH_ACTIVITY=$("$ADB" shell cmd package resolve-activity --brief \
  -c android.intent.category.LAUNCHER "$PACKAGE" | tr -d '\r' | tail -1)
case "$LAUNCH_ACTIVITY" in
  "$PACKAGE"/*) ;;
  *) echo "FAIL no launchable activity for $PACKAGE (got: $LAUNCH_ACTIVITY)"; exit 1 ;;
esac
"$ADB" shell am start -n "$LAUNCH_ACTIVITY" >/dev/null
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

# The packaged WebView exposes almost nothing to accessibility and drops console output
# in a release build, so a tap cannot be verified by asking the page. It can be verified
# by looking: tools/harness/screen-of.py names the screen in a screenshot from one
# colour each screen alone has. A tap that lands on nothing is a failure, not a pass.
PYTHON=.venv/bin/python
[ -x "$PYTHON" ] || PYTHON=python3
SHOT=$(mktemp -t pothole-smoke).png
current_screen() {
  "$ADB" exec-out screencap -p > "$SHOT" 2>/dev/null
  "$PYTHON" tools/harness/screen-of.py "$SHOT" 2>/dev/null || echo unknown
}
await_screen() {  # await_screen <name...> <seconds>: succeeds on any listed name
  local limit=${@: -1} waited=0 seen
  local names=("${@:1:$#-1}")
  while [ $waited -lt "$limit" ]; do
    seen=$(current_screen)
    for name in "${names[@]}"; do [ "$seen" = "$name" ] && { LAST_SCREEN=$seen; return 0; }; done
    sleep 1; waited=$((waited + 1))
  done
  LAST_SCREEN=$seen
  return 1
}
tap_until() {  # tap_until <x> <y> <screen> <attempts>
  local x=$1 y=$2 want=$3 attempts=$4 n=0
  while [ $n -lt "$attempts" ]; do
    "$ADB" shell input tap "$x" "$y"
    await_screen "$want" 6 && return 0
    n=$((n + 1))
  done
  return 1
}
alive() { "$ADB" shell pidof "$PACKAGE" 2>/dev/null | tr -d '\r'; }

# The emulator's own System UI can stall under load and put an "isn't responding" dialog
# over everything, dimming the screen so no pixel probe matches. It belongs to systemui,
# not the app, and its buttons are exposed to accessibility: press Wait.
dismiss_system_anr() {
  local n=0 target
  while [ $n -lt 5 ]; do
    "$ADB" shell dumpsys window 2>/dev/null | grep -q "Application Not Responding" || return 0
    "$ADB" shell rm -f /sdcard/ui.xml >/dev/null 2>&1
    "$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
    target=$("$ADB" shell cat /sdcard/ui.xml 2>/dev/null |
      python3 "$(dirname "$0")/permission-button.py" "Wait") || target=""
    [ -n "$target" ] && "$ADB" shell input tap $target
    sleep 2
    n=$((n + 1))
  done
}

echo "4/6 first run opens on Home"
pid_start="$(alive)"
dismiss_system_anr
# A fresh install lands on Home. There is no onboarding form to clear: shared detection
# is the default and needs no key. If Settings ever comes back as the first screen, this
# is where the smoke test says so.
await_screen home 20 || { echo "FAIL a fresh install did not open on Home (saw: $LAST_SCREEN)"; exit 1; }

echo "5/6 Drive, Continue on the camera and location notice, both permissions"
sleep 2
dismiss_system_anr
tap_until 540 455 dataConsent 4 || { echo "FAIL Drive did not open the camera and location notice (saw: $LAST_SCREEN)"; exit 1; }
sleep 1
# The notice's green Continue is the lower-right button. This is the tap that killed
# the first 1.39.0 release build.
"$ADB" shell input tap 780 2322
sleep 4
if [ -z "$(alive)" ] || [ "$(alive)" != "$pid_start" ]; then
  echo "FAIL the app process died after Continue on the camera and location notice"
  "$ADB" logcat -d 2>/dev/null | grep -A 12 "FATAL EXCEPTION" | head -16
  exit 1
fi
# Camera, then location: Android asks for each in its own sheet, and the sheets differ
# in height, so the grant button is not at a fixed place. The sheets belong to the system
# permission controller, which unlike the app's WebView is exposed to accessibility, so
# ask it where the button is instead of guessing at pixels.
grant_visible_permission() {
  "$ADB" shell dumpsys window 2>/dev/null | grep -q GrantPermissionsActivity || return 1
  "$ADB" shell rm -f /sdcard/ui.xml >/dev/null 2>&1
  "$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  local target
  target=$("$ADB" shell cat /sdcard/ui.xml 2>/dev/null |
    python3 "$(dirname "$0")/permission-button.py") || return 1
  [ -n "$target" ] || return 1
  "$ADB" shell input tap $target
  sleep 3
  return 0
}

granted=0
for _ in 1 2 3 4 5 6 7 8; do
  if grant_visible_permission; then
    granted=$((granted + 1))
  else
    # No dialog on screen. One may still be on its way, so look again before moving on.
    sleep 2
    "$ADB" shell dumpsys window 2>/dev/null | grep -q GrantPermissionsActivity || break
  fi
done
echo "   granted $granted permission dialog(s)"
await_screen drive 15 || { echo "FAIL Drive did not reach the live camera screen (saw: $LAST_SCREEN)"; exit 1; }
if [ -z "$(alive)" ] || [ "$(alive)" != "$pid_start" ]; then
  echo "FAIL the app process died while Drive was starting"; exit 1
fi

echo "6/6 checking for JavaScript errors and crashes"
errors="$("$ADB" logcat -d 2>/dev/null \
  | grep -iE "FATAL EXCEPTION|Uncaught|is not defined|ReferenceError|TypeError|SyntaxError" \
  | grep -viE "AppsFilter|PreferenceController|BaseSearchIndex|Phenotype|BinderNative|uiautomator" || true)"
if [ -n "$errors" ]; then
  echo "FAIL the app reported errors on first run:"
  echo "$errors" | head -10
  exit 1
fi
rm -f "$SHOT"

# Focus is briefly null while a permission sheet dismisses; ask more than once.
screen=0
for _ in 1 2 3 4 5; do
  screen="$("$ADB" shell dumpsys window 2>/dev/null \
    | grep -cE "(mCurrentFocus|mFocusedApp).*$PACKAGE" || true)"
  [ "$screen" != "0" ] && break
  sleep 1
done
[ "$screen" != "0" ] || { echo "FAIL the app left the foreground during first run"; exit 1; }

[ "$KEEP" = "1" ] || "$ADB" shell am force-stop "$PACKAGE" >/dev/null
echo "emulator smoke passed: fresh install reached Home, the notice, both permissions and the live drive with no errors"
