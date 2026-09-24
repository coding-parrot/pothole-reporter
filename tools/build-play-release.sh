#!/usr/bin/env bash
# Build signed Play and direct-install release artifacts without copying or
# mutating any web source files.
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
APK_PATH=$ANDROID_ROOT/app/build/outputs/apk/release/app-release.apk
R8_MAPPING_PATH=$ANDROID_ROOT/app/build/outputs/mapping/release/mapping.txt
# AGP 8.13 writes the fully merged, packaged release manifest here. Validate this
# generated artifact rather than an obsolete pre-8.13 intermediate path.
BUNDLE_MANIFEST=$ANDROID_ROOT/app/build/intermediates/packaged_manifests/release/processReleaseManifestForPackage/AndroidManifest.xml
WWW_ROOT=android-app/www
PACKAGED_ASSETS_ROOT=$ANDROID_ROOT/app/src/main/assets/public
SOURCE_CAPACITOR_CONFIG=android-app/capacitor.config.json
PACKAGED_CAPACITOR_CONFIG=$ANDROID_ROOT/app/src/main/assets/capacitor.config.json
RELEASE_ASSET_VERIFIER=tools/verify-release-assets.py
PACK_MANIFEST=static/pack-manifest-v1.35.json
PREVIOUS_PACK_MANIFEST=static/pack-manifest-v1.33.json
V131_PACK_MANIFEST=static/pack-manifest-v1.31.json
V130_PACK_MANIFEST=static/pack-manifest-v1.30.json
V129_PACK_MANIFEST=static/pack-manifest-v1.29.json
V128_PACK_MANIFEST=static/pack-manifest-v1.28.json
V127_PACK_MANIFEST=static/pack-manifest-v1.27.json
INITIAL_PACK_MANIFEST=static/pack-manifest-v1.26.json
LEGACY_PACK_MANIFEST=static/pack-manifest.json
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

same_json() {
  local expected=$1
  local actual=$2
  local label=$3
  [ -f "$expected" ] || fail "$label source is missing: $expected"
  [ -f "$actual" ] || fail "$label is missing: $actual"
  python3 -c 'import json, sys; expected = json.load(open(sys.argv[1], encoding="utf-8")); actual = json.load(open(sys.argv[2], encoding="utf-8")); raise SystemExit(expected != actual)' \
    "$expected" "$actual" || fail "$label differs: $expected != $actual"
}

json_in_zip_matches() {
  local expected=$1
  local archive=$2
  local member=$3
  local label=$4
  python3 -c 'import json, sys, zipfile; expected = json.load(open(sys.argv[1], encoding="utf-8")); archive = zipfile.ZipFile(sys.argv[2]); actual = json.loads(archive.read(sys.argv[3])); raise SystemExit(expected != actual)' \
    "$expected" "$archive" "$member" || fail "$label differs from $expected"
}

require_tool cmp
require_tool find
require_tool grep
require_tool head
require_tool jarsigner
require_tool keytool
require_tool node
require_tool sed
require_tool shasum
require_tool sort
require_tool stat
require_tool tail
require_tool tr
require_tool unzip

echo "1/8 checking the generated LLM contract and native contracts (read only)"
node llm/generate.mjs --check
# The user agent suffix follows versionName, and every manifest permission has a user.
node tools/harness/check-native-contracts.mjs

echo "2/8 checking web-source mirrors (read only)"
[ -d static ] || fail "static source directory is missing"
[ -d docs ] || fail "hosted docs directory is missing"
[ -d "$WWW_ROOT" ] || fail "Android www source directory is missing"
[ -d "$PACKAGED_ASSETS_ROOT" ] || fail "packaged Android assets directory is missing"
[ -f "$SOURCE_CAPACITOR_CONFIG" ] || fail "source Capacitor config is missing"
[ -f "$PACKAGED_CAPACITOR_CONFIG" ] || fail "packaged Capacitor config is missing"
[ -f "$RELEASE_ASSET_VERIFIER" ] || fail "release asset verifier is missing"
[ -x "$ANDROID_ROOT/gradlew" ] || fail "Gradle wrapper is missing or not executable"
python3 tools/build-state-packs.py --check
python3 tools/build-national-highways.py --check
python3 tools/build-highway-contract-packs.py --check
python3 tools/build-gepnic-road-notice-packs.py --check
python3 tools/build-pmgsy-road-agreement-packs.py --check
python3 tests/state_pack_validation_test.py
same_file "$PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.35.json" "v1.35 pack manifest mirror"
same_file "$PREVIOUS_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.33.json" "v1.33 pack manifest mirror"
same_file "$V131_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.31.json" "v1.31 pack manifest mirror"
same_file "$V130_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.30.json" "v1.30 pack manifest mirror"
same_file "$V129_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.29.json" "v1.29 pack manifest mirror"
same_file "$V128_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.28.json" "v1.28 pack manifest mirror"
same_file "$V127_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.27.json" "v1.27 pack manifest mirror"
same_file "$INITIAL_PACK_MANIFEST" "$WWW_ROOT/pack-manifest-v1.26.json" "v1.26 pack manifest mirror"
same_file "$LEGACY_PACK_MANIFEST" "$WWW_ROOT/pack-manifest.json" "legacy pack manifest mirror"
same_file "$HIGHWAY_MANIFEST" "$WWW_ROOT/highway-manifest.json" "highway manifest mirror"
for asset in "${FORBIDDEN_STATE_ASSETS[@]}"; do
  [ ! -e "static/$asset" ] || fail "state data must not be bundled in static/: $asset"
  [ ! -e "$WWW_ROOT/$asset" ] || fail "state data must not be bundled in Android www/: $asset"
  [ ! -e "$PACKAGED_ASSETS_ROOT/$asset" ] || fail "state data must not be bundled in packaged Android assets: $asset"
done
python3 "$RELEASE_ASSET_VERIFIER" \
  --static static --www "$WWW_ROOT" --docs docs --packaged "$PACKAGED_ASSETS_ROOT"
same_json "$SOURCE_CAPACITOR_CONFIG" "$PACKAGED_CAPACITOR_CONFIG" \
  "packaged Capacitor runtime config"

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

echo "3/8 building signed release artifacts"
rm -f "$AAB_PATH" "$APK_PATH"
# Both, in one invocation. bundleRelease alone never writes an APK, so the checks and
# the SHA-256 this script prints for it described whatever stale file was left behind.
(cd "$ANDROID_ROOT" && ./gradlew --no-daemon --offline :app:bundleRelease :app:assembleRelease -q)
[ -s "$AAB_PATH" ] || fail "Gradle produced no non-empty AAB"
[ -s "$APK_PATH" ] || fail "Gradle produced no non-empty release APK"
[ -s "$R8_MAPPING_PATH" ] || fail "R8 mapping is missing; release code shrinking is not active"
[ -f "$BUNDLE_MANIFEST" ] || fail "Gradle produced no release bundle manifest"
if ! unzip -Z1 "$AAB_PATH" | grep -Fx 'BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map' >/dev/null; then
  fail "AAB does not contain the R8 deobfuscation mapping"
fi

echo "4/8 validating release identity and manifest policy"
grep -Fq 'package="dev.aiengg.potholereporter"' "$BUNDLE_MANIFEST" || fail "unexpected application ID"
grep -Fq 'android:versionCode="74"' "$BUNDLE_MANIFEST" || fail "expected versionCode 74"
grep -Fq 'android:versionName="1.39.5"' "$BUNDLE_MANIFEST" || fail "expected versionName 1.39.5"
grep -Fq 'android:allowBackup="false"' "$BUNDLE_MANIFEST" || fail "allowBackup must remain false"
grep -Fq 'android:dataExtractionRules="@xml/data_extraction_rules"' "$BUNDLE_MANIFEST" || fail "data extraction exclusions are missing"
grep -Fq 'android:fullBackupContent="@xml/backup_rules"' "$BUNDLE_MANIFEST" || fail "legacy backup exclusions are missing"
grep -Fq 'com.bmc.potholequickfix' "$BUNDLE_MANIFEST" || fail "BMC Pothole QuickFix package query is missing"
grep -Fq 'com.newnmmc.app' "$BUNDLE_MANIFEST" || fail "My NMMC package query is missing"
grep -Fq 'com.nyatitechnologies.pmcroadmitra' "$BUNDLE_MANIFEST" || fail "PMC Road Mitra package query is missing"
grep -Fq 'com.kmc.app' "$BUNDLE_MANIFEST" || fail "official KMC app package query is missing"
grep -Fq 'com.sis.pwdsewaapp' "$BUNDLE_MANIFEST" || fail "official PWD Sewa app package query is missing"
grep -Fq 'com.ceedeev.grivenancev2' "$BUNDLE_MANIFEST" || fail "official Namma Chennai app package query is missing"
grep -Fq 'org.tnega.cmhelpline.citizen' "$BUNDLE_MANIFEST" || fail "official Mudhalvarin Mugavari app package query is missing"
grep -Fq 'cgg.gov.ghmc' "$BUNDLE_MANIFEST" || fail "official My Cure app package query is missing"
grep -Fq 'com.amplvb.ccrs' "$BUNDLE_MANIFEST" || fail "official AMC CCRS app package query is missing"
grep -Fq 'com.nhai.rajmargyatra' "$BUNDLE_MANIFEST" || fail "official Rajmargyatra app package query is missing"
grep -Fq 'com.nammabengaluruNew.org' "$BUNDLE_MANIFEST" || fail "official Sahaaya app package query is missing"
grep -Fq 'com.esri.ugms_bmc' "$BUNDLE_MANIFEST" || fail "official BMC MARG app package query is missing"
grep -Fq 'in.gov.pmc.pmccare' "$BUNDLE_MANIFEST" || fail "official PMC CARE app package query is missing"
grep -Fq 'com.nic.dl.delhijanmitra' "$BUNDLE_MANIFEST" || fail "official Delhi JanSunwai app package query is missing"
grep -Fq 'in.nic.up.jansunwai.upjansunwai' "$BUNDLE_MANIFEST" || fail "official UP Jansunwai app package query is missing"
grep -Fq 'com.rajsampark.versiontwo' "$BUNDLE_MANIFEST" || fail "official Rajasthan Sampark 2.0 package query is missing"
grep -Fq 'in.gov.dpg.cmhelpline' "$BUNDLE_MANIFEST" || fail "official CM Helpline Goa app package query is missing"
grep -Fq 'com.magnum.helpline' "$BUNDLE_MANIFEST" || fail "official MP CM Helpline app package query is missing"
grep -Fq 'com.bpsms.jansamadhan' "$BUNDLE_MANIFEST" || fail "official Bihar Jan Samadhan app package query is missing"
grep -Fq 'com.sociomatic.janasunani' "$BUNDLE_MANIFEST" || fail "official Odisha Jana Sunani app package query is missing"
grep -Fq 'com.google.android.apps.maps' "$BUNDLE_MANIFEST" || fail "Google Maps package query is missing"
grep -Fq 'com.gauravsen.potholereporter.drivemode.DriveModeService' "$BUNDLE_MANIFEST" || fail "native Drive foreground service is missing"
# No foreground service types: Play requires a demonstration of each declared type, and
# the only path that would use one is unreachable in this build. The untyped
# FOREGROUND_SERVICE permission stays because androidx.work declares it, and on its own
# it does not trigger Play's foreground service declaration.
! grep -q 'android:foregroundServiceType=' "$BUNDLE_MANIFEST" || fail "a foreground service type is declared; Play will demand a demonstration of it"

if grep -Eq 'android:(debuggable|testOnly)="true"' "$BUNDLE_MANIFEST"; then
  fail "release manifest is debuggable or test-only"
fi
if grep -Fq 'android:requestLegacyExternalStorage=' "$BUNDLE_MANIFEST"; then
  fail "legacy external-storage mode leaked into the release manifest"
fi

actual_permissions=$(sed -n 's/.*<uses-permission android:name="\([^"]*\)".*/\1/p' "$BUNDLE_MANIFEST" | sort -u)
expected_permissions=$'android.permission.ACCESS_COARSE_LOCATION\nandroid.permission.ACCESS_FINE_LOCATION\nandroid.permission.ACCESS_NETWORK_STATE\nandroid.permission.CAMERA\nandroid.permission.FOREGROUND_SERVICE\nandroid.permission.INTERNET\nandroid.permission.RECEIVE_BOOT_COMPLETED\nandroid.permission.WAKE_LOCK\ndev.aiengg.potholereporter.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
if [ "$actual_permissions" != "$expected_permissions" ]; then
  echo "Expected permissions:" >&2
  printf '%s\n' "$expected_permissions" >&2
  echo "Actual permissions:" >&2
  printf '%s\n' "$actual_permissions" >&2
  fail "release permission set changed; review it before publishing"
fi

echo "5/8 validating the AAB signature"
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
# Do not accept an arbitrary non-debug key. Android treats a different signer as a
# different application and reports the sideloaded update as invalid/incompatible.
expected_upload_cert_sha256=296f947f8412aca3925cf5169c195ae097c6856d5751eedd789dd4bfba7bac8c
actual_upload_cert_sha256=$(keytool -printcert -jarfile "$AAB_PATH" 2>/dev/null \
  | sed -n 's/^[[:space:]]*SHA256: //p' | tr -d ':' | tr '[:upper:]' '[:lower:]' | head -n 1)
[ "$actual_upload_cert_sha256" = "$expected_upload_cert_sha256" ] \
  || fail "release signer differs from the established Pothole Reporter upload certificate"

echo "6/8 validating the APK signature"
APKSIGNER=$(command -v apksigner || true)
if [ -z "$APKSIGNER" ]; then
  # Gradle finds the SDK through local.properties, so look there too rather than
  # demanding an environment variable this build does not otherwise need.
  sdk_dir=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}
  if [ -z "$sdk_dir" ] && [ -f "$ANDROID_ROOT/local.properties" ]; then
    sdk_dir=$(sed -n 's/^sdk\.dir=//p' "$ANDROID_ROOT/local.properties" | tail -n 1)
  fi
  if [ -n "$sdk_dir" ] && [ -d "$sdk_dir/build-tools" ]; then
    APKSIGNER=$(find "$sdk_dir/build-tools" -name apksigner -type f 2>/dev/null \
      | sort | tail -n 1)
  fi
fi
[ -n "$APKSIGNER" ] || fail "apksigner was not found on PATH, in ANDROID_SDK_ROOT/ANDROID_HOME, or under the sdk.dir in local.properties"
apk_signature_report=$("$APKSIGNER" verify --verbose --print-certs "$APK_PATH" 2>&1 || true)
grep -Fq "Verified using v2 scheme (APK Signature Scheme v2): true" <<<"$apk_signature_report" \
  || fail "release APK is not signed with APK Signature Scheme v2"
actual_apk_cert_sha256=$(sed -n 's/^Signer #1 certificate SHA-256 digest: //p' \
  <<<"$apk_signature_report" | tr '[:upper:]' '[:lower:]' | head -n 1)
[ "$actual_apk_cert_sha256" = "$expected_upload_cert_sha256" ] \
  || fail "APK signer does not match the registered Pothole Reporter upload certificate"

echo "7/8 verifying bundled web assets"
while IFS= read -r source_file; do
  relative_path=${source_file#"$PACKAGED_ASSETS_ROOT"/}
  if ! diff -q <(unzip -p "$AAB_PATH" "base/assets/public/$relative_path") "$source_file" >/dev/null; then
    fail "AAB asset differs from source: $relative_path"
  fi
done < <(find "$PACKAGED_ASSETS_ROOT" -type f -print | sort)

while IFS= read -r packaged_js; do
  if unzip -p "$AAB_PATH" "base/assets/public/$packaged_js" | grep -Eqa 'sk-(proj-)?[A-Za-z0-9_-]{20,}'; then
    fail "an API-key-shaped value is embedded in $packaged_js"
  fi
done < <(find "$PACKAGED_ASSETS_ROOT" -type f -name '*.js' -print \
  | sed "s#^$PACKAGED_ASSETS_ROOT/##" | sort)

python3 "$RELEASE_ASSET_VERIFIER" \
  --static static --www "$WWW_ROOT" --docs docs --packaged "$PACKAGED_ASSETS_ROOT" \
  --aab "$AAB_PATH" --apk "$APK_PATH"

echo "8/8 release artifacts accepted"
bundle_bytes=$(stat -f%z "$AAB_PATH" 2>/dev/null || stat -c%s "$AAB_PATH")
bundle_sha256=$(shasum -a 256 "$AAB_PATH" | sed 's/[[:space:]].*//')
apk_bytes=$(stat -f%z "$APK_PATH" 2>/dev/null || stat -c%s "$APK_PATH")
apk_sha256=$(shasum -a 256 "$APK_PATH" | sed 's/[[:space:]].*//')
printf 'AAB OK  %s bytes  SHA-256 %s\n%s\n' \
  "$bundle_bytes" "$bundle_sha256" "$PROJECT_ROOT/$AAB_PATH"
printf 'APK OK  %s bytes  SHA-256 %s\n%s\n' \
  "$apk_bytes" "$apk_sha256" "$PROJECT_ROOT/$APK_PATH"
