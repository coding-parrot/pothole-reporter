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
# Cost: each phone is cut off at 10 minutes, so one run is at most 40 device minutes.
# Free while the account's trial minutes last, then USD 0.17 a device minute.
#
# Other forms:
#   phone-test.sh <apk> --only "Galaxy A15"   one phone, for trying a change to the test
#   phone-test.sh <apk> --no-wait             schedule and return; prints the run ARN
#   phone-test.sh --status <run arn>          one status line
#   phone-test.sh --collect <run arn>         results of a finished run
#
# Only the APK (the file that ships to Play) and the test package are uploaded. The
# keystore, its password and .env never leave the Mac; the script refuses such files.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-pothole}"
exec python3 "$(dirname "$0")/device-farm/run.py" "$@"
