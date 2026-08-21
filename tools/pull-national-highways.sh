#!/usr/bin/env bash
# Rebuild the nationwide NH/NE tiles from the reviewed immutable India extract.
set -euo pipefail

cd "$(dirname "$0")/.."
SOURCE_URL=https://download.geofabrik.de/asia/india-260820.osm.pbf
SOURCE_MD5=c5e0a62a1cb00c80d8c5948bf18370d7
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pothole-national-highways.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v osmium >/dev/null || { echo "osmium-tool is required" >&2; exit 1; }

PBF=$WORK_DIR/india.osm.pbf
FILTERED=$WORK_DIR/national-highways.osm.pbf
GEOJSONSEQ=$WORK_DIR/national-highways.geojsonseq

curl --fail --location --retry 3 --output "$PBF" "$SOURCE_URL"
if command -v md5 >/dev/null; then
  ACTUAL_MD5=$(md5 -q "$PBF")
else
  ACTUAL_MD5=$(md5sum "$PBF" | cut -d' ' -f1)
fi
[ "$ACTUAL_MD5" = "$SOURCE_MD5" ] || {
  echo "source checksum mismatch: $ACTUAL_MD5" >&2
  exit 1
}

osmium tags-filter "$PBF" 'w/ref=NH*' 'w/ref=NE*' 'w/network=IN:NH' \
  --overwrite --output "$FILTERED"
osmium export "$FILTERED" --geometry-types=linestring \
  --output-format=geojsonseq --overwrite --output "$GEOJSONSEQ"
python3 tools/build-national-highways.py --source "$GEOJSONSEQ"
