#!/usr/bin/env bash
# The whole v2 run on the training instance, stage by stage. Every stage is resumable
# and leaves a marker in work/.done/; a rerun skips what is done. After each stage its
# log goes to s3://$ML_BUCKET/runs/$RUN_ID/logs/ and one line is appended to
# runs/$RUN_ID/progress.md, so the state of the run is one `aws s3 cp` away.
#
#   nohup setsid ml/classifier/cloud/run.sh > /opt/ml/run.log 2>&1 &
#   STAGES="evaluate export" ml/classifier/cloud/run.sh        # only these
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
unset AWS_PROFILE  # the instance role
CLASSIFIER="$(cd "$HERE/.." && pwd)"
WORK="$CLASSIFIER/work"
RUN_ID="${RUN_ID:-screen-v2-20261007}"
S3="s3://$ML_BUCKET"
PYTHON="${PYTHON:-/opt/pytorch/bin/python}"
VERSION="${VERSION:-road-screen-v2-mobilenetv3l-448}"
export PYTHON DETECTOR_SECRET_ARN PATH="/opt/node/bin:$PATH"
export TEACHER_MAX_USD="${TEACHER_MAX_USD:-40}"
mkdir -p "$WORK/logs" "$WORK/.done"
cd "$CLASSIFIER"

progress() {
  echo "- $(date -u +%Y-%m-%dT%H:%M:%SZ) $1" >> "$WORK/progress.md"
  aws s3 cp "$WORK/progress.md" "$S3/runs/$RUN_ID/progress.md" --only-show-errors || true
}

stage() {  # name, then the command
  local name="$1"; shift
  if [[ -e "$WORK/.done/$name" ]]; then echo "$name: done before"; return 0; fi
  echo "$name: start $(date -u +%H:%M:%S)"
  local started=$SECONDS status=0
  "$@" > "$WORK/logs/$name.log" 2>&1 || status=$?
  aws s3 cp "$WORK/logs/$name.log" "$S3/runs/$RUN_ID/logs/$name.log" --only-show-errors || true
  if [[ $status -ne 0 ]]; then
    progress "$name FAILED after $((SECONDS - started))s: $(tail -n 3 "$WORK/logs/$name.log" | tr '\n' ' ' | cut -c1-300)"
    exit 1
  fi
  touch "$WORK/.done/$name"
  progress "$name done in $((SECONDS - started))s. $(tail -n 1 "$WORK/logs/$name.log" | cut -c1-300)"
}

setup() {
  "$PYTHON" -m pip install -q timm==1.0.30
  if [[ ! -x /opt/node/bin/node ]]; then
    local file
    file="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | grep -o 'node-v22[0-9.]*-linux-x64.tar.xz' | head -n 1)"
    curl -fsSL "https://nodejs.org/dist/latest-v22.x/$file" -o /tmp/node.tar.xz
    curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | grep " $file\$" | sed "s# $file# /tmp/node.tar.xz#" | sha256sum -c -
    mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components 1 && rm /tmp/node.tar.xz
  fi
  (cd lambda && npm ci --no-audit --no-fund && npm test)
  "$PYTHON" -c "import torch, timm; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), 'timm', timm.__version__)"
}

restore_v1() {
  # The v1 working data: frames the teacher already judged, its answers, the v1 heads
  # and ONNX. Private drive frames included, so it comes from the private bucket only.
  for part in frames teacher owner heads onnx rdd2022_india/train/annotations; do
    aws s3 sync "$S3/v1-work/$part" "$WORK/$part" --only-show-errors
  done
  aws s3 cp "$S3/v1-work/manifest.jsonl" "$WORK/manifest-v1.jsonl" --only-show-errors
  echo "v1 frames: $(wc -l < "$WORK/frames/index.jsonl") indexed, $(find "$WORK/teacher" -name '*.json' | wc -l) teacher answers, $(wc -l < "$WORK/manifest-v1.jsonl") manifest rows"
}

prepare() {
  "$PYTHON" datasets.py prepare
  aws s3 sync "$WORK/frames" "$S3/frames/" --only-show-errors --exclude "desktop-*" --exclude "downloads-*" --exclude "rdd2022-india-*"
  aws s3 cp "$WORK/selection.json" "$S3/frames/selection.json" --only-show-errors
  echo "prepared: $(wc -l < "$WORK/frames/index.jsonl") frames indexed"
}

label() {
  "$PYTHON" datasets.py paths
  "$PYTHON" teacher_label.py --max-usd "$TEACHER_MAX_USD" --concurrency 48 --paths-file "$WORK/label-once.txt"
  # Validation and test frames get a second, independent answer: it measures the teacher.
  "$PYTHON" teacher_label.py --max-usd "$TEACHER_MAX_USD" --concurrency 48 --paths-file "$WORK/label-twice.txt" --trial 1
  tar -C "$WORK" -cf "$WORK/teacher.tar" teacher
  aws s3 cp "$WORK/teacher.tar" "$S3/labels/teacher.tar" --only-show-errors && rm "$WORK/teacher.tar"
  "$PYTHON" teacher_label.py --max-usd "$TEACHER_MAX_USD" --paths-file "$WORK/label-once.txt" | tail -n 2
}

manifest() {
  "$PYTHON" build_manifest.py
  aws s3 cp "$WORK/manifest.jsonl" "$S3/labels/manifest.jsonl" --only-show-errors
  aws s3 cp "$WORK/manifest-summary.json" "$S3/labels/manifest-summary.json" --only-show-errors
  echo "manifest: $(wc -l < "$WORK/manifest.jsonl") frames"
}

probes() {
  # v1 scored on today's frames, the v1 recipe on the new data, and the same with
  # augmented views. Frozen encoders, so this is minutes.
  for encoder in mobilenetv3_l efficientnet_b0; do
    "$PYTHON" embed.py --encoder "$encoder" --size 448 --views 4 --batch 64
    "$PYTHON" train_probe.py --encoder "$encoder" --size 448 --name v2probe
    "$PYTHON" train_probe.py --encoder "$encoder" --size 448 --name v2probe_aug --views 4
  done
  "$PYTHON" train_probe.py --encoder mobilenetv3_l --size 448 --name v1 --score-head hidden256
  aws s3 sync "$WORK/results" "$S3/runs/$RUN_ID/report/results/" --only-show-errors
}

finetunes() {
  # Full fine-tunes with augmentation. Each run checkpoints every epoch and is skipped
  # once its scores exist.
  for job in ${FINETUNE_JOBS:-mobilenetv3_l:1 efficientnet_b0:1 mobilenetv3_l:2 mobilenetv3_l:3 efficientnet_b0:2}; do
    local encoder="${job%%:*}" seed="${job##*:}"
    local name="ft_s$seed"
    if [[ ! -s "$WORK/scores/${encoder}_448_${name}.npz" ]]; then
      "$PYTHON" finetune.py --encoder "$encoder" --size 448 --name "$name" --seed "$seed" \
        --head-from v2probe_aug ${FINETUNE_ARGS:-}
    fi
    aws s3 cp "$WORK/finetuned/${encoder}_448_${name}.body.pt" "$S3/runs/$RUN_ID/checkpoints/${encoder}_448_${name}.body.pt" --only-show-errors
    aws s3 cp "$WORK/heads/${encoder}_448_${name}.pt" "$S3/runs/$RUN_ID/checkpoints/${encoder}_448_${name}.head.pt" --only-show-errors
    aws s3 sync "$WORK/results" "$S3/runs/$RUN_ID/report/results/" --only-show-errors
    progress "fine-tune ${encoder} seed ${seed} done: $("$PYTHON" -c "import json,sys; d=json.load(open(sys.argv[1])); print('best epoch', d['best']['epoch'], 'validation cleared at 98% recall', round(d['best']['value'], 4))" "$WORK/results/finetune_${encoder}_448_${name}.json")"
  done
}

evaluate() {
  local variants
  variants="$(cd "$WORK/scores" && ls *.npz | grep -v '^served_' | sed 's/\.npz$//' | grep -v '^mobilenetv3_l_448_v1$' | tr '\n' ' ')"
  "$PYTHON" evaluate.py --baseline mobilenetv3_l_448_v1 --variants $variants --run-id "$RUN_ID" --out "$WORK/report/training-path" > /dev/null
  aws s3 sync "$WORK/report" "$S3/runs/$RUN_ID/report/" --only-show-errors
  aws s3 sync "$WORK/scores" "$S3/runs/$RUN_ID/report/scores/" --only-show-errors
  echo "evaluated: $variants"
}

export_model() {
  # The release candidate is the MobileNetV3-L variant with the best VALIDATION number
  # (EfficientNet-B0 at 448 measured 162 ms on the Lambda in v1, over the 150 ms bar).
  local pick
  pick="$("$PYTHON" cloud/pick.py)"
  echo "release candidate: $pick"
  local weights=()
  [[ -f "$WORK/finetuned/mobilenetv3_l_448_${pick}.body.pt" ]] && weights=(--weights "$WORK/finetuned/mobilenetv3_l_448_${pick}.body.pt")
  "$PYTHON" release.py --encoder mobilenetv3_l --size 448 --head "$pick" "${weights[@]}" --version "$VERSION"
  "$PYTHON" release.py --existing "$WORK/onnx/mobilenetv3_l_448_hidden256.onnx" --version road-screen-v1-mobilenetv3l-448
  # The authoritative scorecard: both models scored through the serving path.
  "$PYTHON" evaluate.py --baseline served_road-screen-v1-mobilenetv3l-448 --variants "served_$VERSION" \
    --run-id "$RUN_ID" --out "$WORK/report/serving-path" > /dev/null
  aws s3 sync "$WORK/release/$VERSION" "$S3/models/$VERSION/" --only-show-errors
  aws s3 sync "$WORK/report" "$S3/runs/$RUN_ID/report/" --only-show-errors
  aws s3 sync "$WORK/scores" "$S3/runs/$RUN_ID/report/scores/" --only-show-errors
  "$PYTHON" -c "import json,sys; d=json.load(open(sys.argv[1])); c=d['comparisons']; print('v2 beats v1 on every slice (serving path):', {k: v['v2_beats_v1_on_every_slice'] for k, v in c.items()})" "$WORK/report/serving-path/report.json"
}

for name in ${STAGES:-setup restore_v1 fetch prepare label manifest probes finetunes evaluate export}; do
  case "$name" in
    fetch) stage fetch "$HERE/fetch_raw.sh" ;;
    export) stage export export_model ;;
    *) stage "$name" "$name" ;;
  esac
done
progress "stages finished: ${STAGES:-all}"
