#!/usr/bin/env bash
# Bring one trained detector from the ML bucket into model/ and write the model.json
# the locator loads: its version, input size, the score at which a frame counts as
# "pothole" (the scorecard's `sure` cut-off unless THRESHOLD is given) and the file's hash.
#
#   ml/detector/lambda/fetch-model.sh pothole_tiny
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="${1:?pothole_tiny or pothole_s}"
RUN_ID="${RUN_ID:-pothole-det-20261009}"
export AWS_PROFILE="${AWS_PROFILE:-pothole}" AWS_REGION="${AWS_REGION:-ap-south-1}"
BUCKET="pothole-reporter-ml-695656921622-ap-south-1"
mkdir -p "$HERE/model"
aws s3 cp "s3://$BUCKET/models/$RUN_ID/$NAME.onnx" "$HERE/model/model.onnx" --only-show-errors
aws s3 cp "s3://$BUCKET/models/$RUN_ID/report.json" "$HERE/model/report.json" --only-show-errors
node - "$HERE/model" "$NAME" "$RUN_ID" "${THRESHOLD:-}" <<'JS'
const fs = require("fs"), crypto = require("crypto");
const [dir, name, run, given] = process.argv.slice(2);
const report = JSON.parse(fs.readFileSync(`${dir}/report.json`)).find((entry) => entry.model === name);
if (!report) throw new Error(`no scorecard for ${name}`);
const threshold = given ? Number(given) : report.cut_offs_from_validation.sure;
if (!(threshold > 0 && threshold < 1)) throw new Error("no usable threshold; pass THRESHOLD=");
const sha256 = crypto.createHash("sha256").update(fs.readFileSync(`${dir}/model.onnx`)).digest("hex");
const meta = { model_version: `${run}-${name.replace("pothole_", "")}`, kind: "yolox", input_size: 640,
  threshold, min_box_score: 0.05, nms: 0.65, parameters: report.parameters, sha256 };
fs.writeFileSync(`${dir}/model.json`, JSON.stringify(meta, null, 1) + "\n");
console.log(JSON.stringify(meta));
JS
rm -f "$HERE/model/report.json"
