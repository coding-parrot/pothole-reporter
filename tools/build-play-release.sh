#!/usr/bin/env bash
# Build a Play upload bundle without copying or mutating any web source files.
#
# Signing values may come from the POTHOLE_RELEASE_* environment variables or
# from the ignored android-app/android/keystore.properties file. Gradle refuses
# to create a release artifact when any signing value is missing.
set -euo pipefail
export LC_ALL=C

cd "$(dirname "$0")/.."
PROJECT_ROOT=$PWD
ANDROID_ROOT=android-app/android
AAB_PATH=$ANDROID_ROOT/app/build/outputs/bundle/release/app-release.aab
BUNDLE_MANIFEST=$ANDROID_ROOT/app/build/intermediates/bundle_manifest/release/processApplicationManifestReleaseForBundle/AndroidManifest.xml
WWW_ROOT=android-app/www
PACKAGED_ASSETS_ROOT=$ANDROID_ROOT/app/src/main/assets/public
PACK_MANIFEST=static/pack-manifest.json
HIGHWAY_MANIFEST=static/highway-manifest.json
FORBIDDEN_STATE_ASSETS=(
  delhi-coverage.json
  karnataka-bodies.json
  kolkata-coverage.json
  maharashtra-coverage.json
  tenders.json
)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || fail "required tool is not installed: $1"
}

same_file() {
  local expected=$1
  local actual=$2
  local label=$3
  [ -f "$actual" ] || fail "$label is missing: $actual"
  cmp -s "$expected" "$actual" || fail "$label differs: $expected != $actual"
}

require_tool cmp
require_tool diff
require_tool find
require_tool grep
require_tool jarsigner
require_tool python3
require_tool sed
require_tool shasum
require_tool sort
require_tool stat
require_tool unzip

echo "1/7 validating hosted data packs, municipal schemas and web-source mirrors (read only)"
[ -d static ] || fail "static source directory is missing"
[ -d "$WWW_ROOT" ] || fail "Android www source directory is missing"
[ -d "$PACKAGED_ASSETS_ROOT" ] || fail "packaged Android assets directory is missing"
[ -x "$ANDROID_ROOT/gradlew" ] || fail "Gradle wrapper is missing or not executable"
python3 tools/build-state-packs.py --check
python3 tools/build-national-highways.py --check
python3 tests/state_pack_validation_test.py
same_file "$PACK_MANIFEST" "$WWW_ROOT/pack-manifest.json" "pack manifest mirror"
same_file "$HIGHWAY_MANIFEST" "$WWW_ROOT/highway-manifest.json" "highway manifest mirror"
for asset in "${FORBIDDEN_STATE_ASSETS[@]}"; do
  [ ! -e "static/$asset" ] || fail "state data must not be bundled in static/: $asset"
  [ ! -e "$WWW_ROOT/$asset" ] || fail "state data must not be bundled in Android www/: $asset"
  [ ! -e "$PACKAGED_ASSETS_ROOT/$asset" ] || fail "state data must not be bundled in packaged Android assets: $asset"
done
while IFS= read -r source_file; do
  relative_path=${source_file#static/}
  same_file "$source_file" "$WWW_ROOT/$relative_path" "static-to-www mirror"
done < <(find static -type f -print | sort)

while IFS= read -r source_file; do
  relative_path=${source_file#"$WWW_ROOT"/}
  same_file "$source_file" "$PACKAGED_ASSETS_ROOT/$relative_path" "www-to-Android mirror"
done < <(find "$WWW_ROOT" -type f -print | sort)

while IFS= read -r packaged_file; do
  relative_path=${packaged_file#"$PACKAGED_ASSETS_ROOT"/}
  case "$relative_path" in
    cordova.js|cordova_plugins.js) continue ;;
  esac
  [ -f "$WWW_ROOT/$relative_path" ] || fail "stale Android public asset is not present in www: $relative_path"
done < <(find "$PACKAGED_ASSETS_ROOT" -type f -print | sort)

echo "2/7 building signed release bundle"
rm -f "$AAB_PATH"
(cd "$ANDROID_ROOT" && ./gradlew --no-daemon --offline :app:bundleRelease -q)
[ -s "$AAB_PATH" ] || fail "Gradle produced no non-empty AAB"
[ -f "$BUNDLE_MANIFEST" ] || fail "Gradle produced no release bundle manifest"

echo "3/7 validating release identity and manifest policy"
grep -Fq 'package="com.gauravsen.potholereporter"' "$BUNDLE_MANIFEST" || fail "unexpected application ID"
grep -Fq 'android:versionCode="38"' "$BUNDLE_MANIFEST" || fail "expected versionCode 38"
grep -Fq 'android:versionName="1.21.0"' "$BUNDLE_MANIFEST" || fail "expected versionName 1.21.0"
grep -Fq 'android:allowBackup="false"' "$BUNDLE_MANIFEST" || fail "allowBackup must remain false"
grep -Fq 'com.bmc.potholequickfix' "$BUNDLE_MANIFEST" || fail "BMC Pothole QuickFix package query is missing"
grep -Fq 'com.newnmmc.app' "$BUNDLE_MANIFEST" || fail "My NMMC package query is missing"
grep -Fq 'com.nyatitechnologies.pmcroadmitra' "$BUNDLE_MANIFEST" || fail "PMC Road Mitra package query is missing"
grep -Fq 'com.kmc.app' "$BUNDLE_MANIFEST" || fail "official KMC app package query is missing"
grep -Fq 'com.sis.pwdsewaapp' "$BUNDLE_MANIFEST" || fail "official PWD Sewa app package query is missing"
grep -Fq 'com.ceedeev.grivenancev2' "$BUNDLE_MANIFEST" || fail "official Namma Chennai app package query is missing"
grep -Fq 'cgg.gov.ghmc' "$BUNDLE_MANIFEST" || fail "official My Cure app package query is missing"
grep -Fq 'com.amplvb.ccrs' "$BUNDLE_MANIFEST" || fail "official AMC CCRS app package query is missing"
grep -Fq 'com.nhai.rajmargyatra' "$BUNDLE_MANIFEST" || fail "official Rajmargyatra app package query is missing"
grep -Fq 'com.google.android.apps.maps' "$BUNDLE_MANIFEST" || fail "Google Maps package query is missing"
grep -Fq 'com.gauravsen.potholereporter.drive.DriveForegroundService' "$BUNDLE_MANIFEST" || fail "native Drive foreground service is missing"
grep -Fq 'android:foregroundServiceType="camera|location"' "$BUNDLE_MANIFEST" || fail "Drive foreground service types are wrong"

if grep -Eq 'android:(debuggable|testOnly)="true"' "$BUNDLE_MANIFEST"; then
  fail "release manifest is debuggable or test-only"
fi
if grep -Fq 'android:requestLegacyExternalStorage=' "$BUNDLE_MANIFEST"; then
  fail "legacy external-storage mode leaked into the release manifest"
fi

actual_permissions=$(sed -n 's/.*<uses-permission android:name="\([^"]*\)".*/\1/p' "$BUNDLE_MANIFEST" | sort -u)
expected_permissions=$'android.permission.ACCESS_COARSE_LOCATION\nandroid.permission.ACCESS_FINE_LOCATION\nandroid.permission.ACCESS_NETWORK_STATE\nandroid.permission.CAMERA\nandroid.permission.FOREGROUND_SERVICE\nandroid.permission.FOREGROUND_SERVICE_CAMERA\nandroid.permission.FOREGROUND_SERVICE_LOCATION\nandroid.permission.INTERNET\nandroid.permission.POST_NOTIFICATIONS\nandroid.permission.WAKE_LOCK\ncom.gauravsen.potholereporter.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
if [ "$actual_permissions" != "$expected_permissions" ]; then
  echo "Expected permissions:" >&2
  printf '%s\n' "$expected_permissions" >&2
  echo "Actual permissions:" >&2
  printf '%s\n' "$actual_permissions" >&2
  fail "release permission set changed; review it before publishing"
fi

echo "4/7 validating the AAB signature"
signature_report=$(jarsigner -verify "$AAB_PATH" 2>&1 || true)
if ! grep -Fq 'jar verified.' <<<"$signature_report" || grep -Fqi 'jar is unsigned' <<<"$signature_report"; then
  fail "AAB is not signed with a verifiable JAR signature"
fi
certificate_report=$(jarsigner -verify -verbose -certs "$AAB_PATH" 2>&1 || true)
if grep -Fqi 'CN=Android Debug' <<<"$certificate_report"; then
  fail "AAB is signed with the Android debug certificate"
fi
if grep -Eqi 'unsigned entries|certificate (has expired|is not yet valid)|disabled algorithm' <<<"$certificate_report"; then
  fail "AAB signature has unsigned entries, an invalid validity period, or a disabled algorithm"
fi

echo "5/7 verifying bundled web assets"
while IFS= read -r source_file; do
  relative_path=${source_file#"$PACKAGED_ASSETS_ROOT"/}
  if ! diff -q <(unzip -p "$AAB_PATH" "base/assets/public/$relative_path") "$source_file" >/dev/null; then
    fail "AAB asset differs from source: $relative_path"
  fi
done < <(find "$PACKAGED_ASSETS_ROOT" -type f -print | sort)

if unzip -p "$AAB_PATH" base/assets/public/standalone.js | grep -Eqa 'sk-(proj-)?[A-Za-z0-9_-]{20,}'; then
  fail "an API-key-shaped value is embedded in standalone.js"
fi
if ! unzip -p "$AAB_PATH" base/assets/capacitor.plugins.json | grep -Fq '@capacitor/app-launcher'; then
  fail "App Launcher plugin is missing from the release bundle"
fi

echo "6/7 confirming large data packs are absent from the AAB"
for asset in "${FORBIDDEN_STATE_ASSETS[@]}"; do
  if unzip -Z1 "$AAB_PATH" | grep -Fx "base/assets/public/$asset" >/dev/null; then
    fail "state data is bundled in the AAB: $asset"
  fi
done
if unzip -Z1 "$AAB_PATH" | grep -Eq '^base/assets/public/packs/v1/highways/'; then
  fail "National Highway geometry tiles are bundled in the AAB"
fi

echo "7/7 release bundle accepted"
bundle_bytes=$(stat -f%z "$AAB_PATH" 2>/dev/null || stat -c%s "$AAB_PATH")
bundle_sha256=$(shasum -a 256 "$AAB_PATH" | sed 's/[[:space:]].*//')
printf 'AAB OK  %s bytes  SHA-256 %s\n%s\n' "$bundle_bytes" "$bundle_sha256" "$PROJECT_ROOT/$AAB_PATH"
