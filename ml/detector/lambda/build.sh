#!/usr/bin/env bash
# Stage the pothole locator Lambda for linux arm64 and zip it. The request and response
# code is the screen's own service.mjs, copied in, so the two cannot drift apart.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCREEN="$HERE/../../classifier/lambda"
BUILD="$HERE/build"
[[ -f "$HERE/model/model.onnx" && -f "$HERE/model/model.json" ]] || {
  echo "model/model.onnx and model/model.json are required (ml/detector/lambda/fetch-model.sh)" >&2; exit 2; }
EXPECTED="$(node -e 'console.log(JSON.parse(require("fs").readFileSync(process.argv[1])).sha256)' "$HERE/model/model.json")"
ACTUAL="$(shasum -a 256 "$HERE/model/model.onnx" | cut -d' ' -f1)"
[[ "$EXPECTED" == "$ACTUAL" ]] || { echo "model.onnx does not match model.json" >&2; exit 2; }

rm -rf "$BUILD"
mkdir -p "$BUILD/package/model"
cp "$HERE"/handler.mjs "$HERE"/locator.mjs "$HERE"/package.json "$HERE"/package-lock.json "$BUILD/package/"
cp "$SCREEN/service.mjs" "$BUILD/package/"
cp "$HERE/model/model.onnx" "$HERE/model/model.json" "$BUILD/package/model/"
(cd "$BUILD/package" && npm ci --omit=dev --os=linux --cpu=arm64 --libc=glibc --ignore-scripts --no-audit --no-fund >/dev/null)
# onnxruntime-node ships every platform's runtime in one package (about 290 MB). Lambda
# allows 250 MB unzipped, and only linux/arm64 can run there.
BIN="$BUILD/package/node_modules/onnxruntime-node/bin"
for dir in "$BIN"/napi-v*/*/*; do
  [[ "$dir" == */linux/arm64 ]] || rm -rf "$dir"
done
find "$BIN" -type d -empty -delete
[[ -f "$(echo "$BIN"/napi-v*/linux/arm64/onnxruntime_binding.node)" ]] || { echo "linux arm64 onnxruntime binding missing" >&2; exit 1; }
[[ -d "$BUILD/package/node_modules/@img/sharp-linux-arm64" ]] || { echo "linux arm64 sharp binary missing" >&2; exit 1; }
(cd "$BUILD/package" && zip -q -r -X "$BUILD/screen-lambda.zip" .)
UNZIPPED_KB="$(du -sk "$BUILD/package" | cut -f1)"
echo "package: $(du -h "$BUILD/screen-lambda.zip" | cut -f1) zipped, $((UNZIPPED_KB / 1024)) MB unzipped (Lambda limit 250 MB)"
[[ "$UNZIPPED_KB" -lt 256000 ]] || { echo "package exceeds Lambda's unzipped limit" >&2; exit 1; }
