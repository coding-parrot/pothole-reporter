#!/usr/bin/env bash
# Download every public archive to work/raw/<name>/ and mirror it to
# s3://$ML_BUCKET/datasets/<name>/raw/. Runs on the training instance. Resumable: a
# dataset with a receipt is skipped, a partial download continues (curl -C -).
# Every source here downloads over plain HTTPS with no account, token or form.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
unset AWS_PROFILE  # the instance role, not a named profile
WORK="$HERE/../work"
PYTHON="${PYTHON:-/opt/pytorch/bin/python}"

# name | file name | URL
SOURCES='
rdd2022|RDD2022.zip|https://ndownloader.figshare.com/files/38030910
irdd-iraq|IRDD_v1.0_final.zip|https://zenodo.org/api/records/21167531/files/IRDD_v1.0_final.zip/content
bucko-dashcam|Potholes_dataset.zip|https://ndownloader.figshare.com/files/37622126
cracks-potholes-brazil|cracks-and-potholes-in-road-images.zip|https://data.mendeley.com/public-files/datasets/t576ydh9v8/files/afc7c028-06e0-475b-b190-e008df681b19/file_downloaded
attain-iran|attain-nykrzdm74f-v1.zip|https://data.mendeley.com/public-api/zip/nykrzdm74f/download/1
rome-road-damage|data.zip|https://zenodo.org/api/records/18528034/files/data.zip/content
bharatpothole|bharatpothole.zip|https://www.kaggle.com/api/v1/datasets/download/surbhisaswatimohanty/bharatpothole
road-damage-alvarobasily|road-damage.zip|https://www.kaggle.com/api/v1/datasets/download/alvarobasily/road-damage
'

receipt() {  # name file url
  local size sha
  size="$(stat -c%s "$2")"
  sha="$(sha256sum "$2" | cut -d' ' -f1)"
  printf '{"name":"%s","file":"%s","url":"%s","bytes":%s,"sha256":"%s","fetched_at":"%s"}\n' \
    "$1" "$(basename "$2")" "$3" "$size" "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

echo "$SOURCES" | while IFS='|' read -r name file url; do
  [[ -n "$name" ]] || continue
  target="$WORK/raw/$name"
  mkdir -p "$target"
  if [[ -s "$target/receipt.json" ]]; then echo "$name: already fetched"; continue; fi
  for attempt in 1 2 3 4 5; do
    if curl -fsSL --retry 5 --retry-delay 5 --connect-timeout 30 --max-time 7200 -C - \
        -A "curl/8" -o "$target/$file" "$url"; then break; fi
    [[ $attempt -lt 5 ]] || { echo "$name: download failed" >&2; exit 1; }
    sleep 15
  done
  unzip -tq "$target/$file" >/dev/null || { echo "$name: archive does not test clean" >&2; exit 1; }
  receipt "$name" "$target/$file" "$url" > "$target/receipt.json"
  aws s3 cp "$target/$file" "s3://$ML_BUCKET/datasets/$name/raw/$file" --only-show-errors
  aws s3 cp "$target/receipt.json" "s3://$ML_BUCKET/datasets/$name/receipt.json" --only-show-errors
  echo "$name: $(cat "$target/receipt.json")"
done

# RAD: the Kaggle archive also holds 5.8 GB of videos. Only images/** is unpacked, into
# the layout the repo's audited adapter (eval/rad_dataset.py) indexes and checks against
# the published counts. The archive's size and hash are compared with the receipt the
# repo already holds for version 3 (eval/rad_v3_source_receipt.json).
target="$WORK/raw/rad-bengaluru"
if [[ ! -s "$target/receipt.json" ]]; then
  mkdir -p "$target"
  url="https://www.kaggle.com/api/v1/datasets/download/rohitsuresh15/radroad-anomaly-detection?datasetVersionNumber=3"
  curl -fsSL --retry 5 --retry-delay 5 --connect-timeout 30 --max-time 7200 -C - -A "curl/8" \
    -o "$target/rad-v3.zip" "$url"
  receipt rad-bengaluru "$target/rad-v3.zip" "$url" > "$target/receipt.pending"
  "$PYTHON" - "$target/receipt.pending" "$HERE/../../../eval/rad_v3_source_receipt.json" <<'PY'
import json, sys
got = json.load(open(sys.argv[1]))
want = json.load(open(sys.argv[2]))["full_archive"]
if (got["bytes"], got["sha256"]) != (want["bytes"], want["sha256"]):
    sys.exit(f"RAD archive differs from the repo's v3 receipt: {got['bytes']} {got['sha256']}")
print("RAD archive matches eval/rad_v3_source_receipt.json")
PY
  rm -rf "$target/files"
  unzip -q "$target/rad-v3.zip" 'images/*' -d "$target/files"
  aws s3 cp "$target/rad-v3.zip" "s3://$ML_BUCKET/datasets/rad-bengaluru/raw/rad-v3.zip" --only-show-errors
  mv "$target/receipt.pending" "$target/receipt.json"
  aws s3 cp "$target/receipt.json" "s3://$ML_BUCKET/datasets/rad-bengaluru/receipt.json" --only-show-errors
  rm -f "$target/rad-v3.zip"
  echo "rad-bengaluru: $(cat "$target/receipt.json")"
fi
echo "raw fetch complete"
