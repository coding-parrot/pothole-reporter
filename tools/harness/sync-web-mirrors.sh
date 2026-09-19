#!/usr/bin/env bash
# Copy the web bundle from static/ to the three mirrors the release gate compares.
#
#   tools/harness/sync-web-mirrors.sh
#
# static/ is the source of truth. android-app/www is what Capacitor copies into the
# APK, docs/ is what GitHub Pages serves (and what the browser suites load, because it
# carries the data packs), and assets/public is the copy already packaged into the
# last build. Editing one and forgetting the others shipped a broken bundle once.
set -euo pipefail
cd "$(dirname "$0")/../.."

PACKAGED=android-app/android/app/src/main/assets/public
for file in $(cd static && ls); do
  for dest in android-app/www docs "$PACKAGED"; do
    [ -d "$dest" ] || continue
    [ -e "$dest/$file" ] || continue
    if [ -d "static/$file" ]; then
      rsync -a --delete "static/$file/" "$dest/$file/"
    else
      cp "static/$file" "$dest/$file"
    fi
  done
done
python3 tools/verify-release-assets.py --static static --www android-app/www \
  --docs docs --packaged "$PACKAGED"
