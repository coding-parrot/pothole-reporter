#!/usr/bin/env bash
# Run the signed release on real phones in AWS Device Farm and report per phone.
#
#   tools/cloud/phone-test.sh <path to signed apk>
#
# Uploads the APK and the Appium test package (tools/cloud/device-farm/), runs the first
# run test on the four phones in devices.json, waits (45 minutes at most, then the run is
# stopped), downloads each phone's result, screenshots and, for a failure, logcat and
# video into ~/Downloads/pothole-testers/device-farm/<version>/run-<time>/, prints a
# pass or fail table, the device minutes used and the trial minutes left, and exits
# non-zero when any phone failed.
#
# On each phone: fresh install, Home, Drive, Continue on the notice, both permission
# sheets, 20 s of live drive left alone, Stop, Settings and back, the app's own record of
# JavaScript errors, then one more drive with Appium closed (two adb taps on a fresh
# process), then the logcat scan for crashes, ANRs and JS errors. A drive fails when the
# HUD says "Camera paused" or the app opened the camera more than once for it.
#
# Cost: each phone is cut off at 10 minutes, so one run is at most 40 device minutes;
# a normal run uses about 11. Free while the account's trial minutes last, then USD 0.17
# a device minute (about USD 1.90 a run). The drives also send about 30 frames a phone to
# the shared production detector (about USD 0.05 of OpenAI a run), and each phone
# registers as a new install.
#
# Other forms:
#   phone-test.sh <apk> --only "Galaxy A15"   one phone, for trying a change to the test
#   phone-test.sh <apk> --extra-drives        two more drives a phone, one with the HUD
#                                             read every second (for chasing a drive bug)
#   phone-test.sh <apk> --no-wait             schedule and return; prints the run ARN
#   phone-test.sh --status <run arn>          one status line
#   phone-test.sh --collect <run arn>         results of a finished run
#
# A debuggable build may be given instead of the release: the test then also records what
# the page sees (video element, camera track, every canvas.toBlob) through the WebView
# context, which a release build does not expose.
#
# Only the APK (the file that ships to Play) and the test package are uploaded. The
# keystore, its password and .env never leave the Mac; the script refuses such files.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-pothole}"
exec python3 "$(dirname "$0")/device-farm/run.py" "$@"
