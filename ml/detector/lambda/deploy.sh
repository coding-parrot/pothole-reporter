#!/usr/bin/env bash
# Create or update the pothole locator Lambda, pothole-reporter-central-locate, with the
# screen's own deploy script: its role, its log group and nothing else. It shares the
# screen's gateway key (the detector secret's yolo_api_key), so the key file must be the
# one the screen was deployed with.
#
#   ml/detector/lambda/fetch-model.sh pothole_tiny
#   SCREEN_API_KEY_FILE=<the screen's key file> ml/detector/lambda/deploy.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# Never a new key: the detector secret already holds this one.
: "${SCREEN_API_KEY_FILE:?set it to the key file the screen was deployed with}"
[[ -s "$SCREEN_API_KEY_FILE" ]] || { echo "$SCREEN_API_KEY_FILE is missing or empty" >&2; exit 2; }
PACKAGE_DIR="$HERE" FUNCTION="${FUNCTION:-pothole-reporter-central-locate}" \
  DESCRIPTION="Pothole locator: a box detector (ml/detector)" \
  SCREEN_API_KEY_FILE="$SCREEN_API_KEY_FILE" exec "$HERE/../../classifier/lambda/deploy.sh"
