#!/usr/bin/env bash
# The whole detector run on the training instance, stage by stage. Every stage leaves a
# marker in $WORK/.done/, its log goes to s3://$ML_BUCKET/runs/$RUN_ID/logs/ and one line
# is appended to runs/$RUN_ID/progress.md. A relaunched instance (a reclaimed spot
# instance, for one) restores the finished stages and the latest checkpoints from
# runs/$RUN_ID/state/ and carries on.
#
#   nohup setsid ml/detector/cloud/run.sh > /opt/ml/run.log 2>&1 &
#   nohup setsid ml/detector/cloud/watchdog.sh > /opt/ml/watchdog.log 2>&1 &
#   STAGES="evaluate export" ml/detector/cloud/run.sh     # only these
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
unset AWS_PROFILE  # the instance role
DETECTOR="$(cd "$HERE/.." && pwd)"
WORK="${DET_WORK:-/opt/ml/det}"
S3="s3://$ML_BUCKET"
STATE="$S3/runs/$RUN_ID/state"
PYTHON="${PYTHON:-/opt/pytorch/bin/python}"
YOLOX=/opt/ml/YOLOX
export DET_RAW="${DET_RAW:-/opt/ml/raw}" DET_OWNER="${DET_OWNER:-/opt/ml/owner}"
export DET_DATA="$WORK/data" DET_RUNS="$WORK/runs" DET_REPORT="$WORK/report"
export PYTHONPATH="$YOLOX:${PYTHONPATH:-}"
# ninja (pip puts it beside python) builds YOLOX's fast COCO scorer at the first evaluation.
export PATH="$(dirname "$PYTHON"):$PATH"
# YOLOX checkpoints hold plain Python and NumPy numbers beside the weights; they are this
# run's own files and the project's published weights, loaded as YOLOX always has.
export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
MODELS="${MODELS:-pothole_tiny pothole_s}"
BATCH="${BATCH:-32}"
mkdir -p "$WORK/logs" "$WORK/.done" "$DET_RUNS" "$DET_REPORT"
cd "$DETECTOR"

progress() {
  echo "- $(date -u +%Y-%m-%dT%H:%M:%SZ) $1" >> "$WORK/progress.md"
  aws s3 cp "$WORK/progress.md" "$S3/runs/$RUN_ID/progress.md" --only-show-errors || true
}

sync_state() {
  aws s3 sync "$WORK/.done" "$STATE/.done" --only-show-errors || true
  aws s3 sync "$DET_RUNS" "$STATE/runs" --only-show-errors --exclude "*" \
    --include "*/latest_ckpt.pth" --include "*/best_ckpt.pth" --include "*/train_log.txt" || true
  aws s3 sync "$DET_REPORT" "$S3/runs/$RUN_ID/report" --only-show-errors || true
  aws s3 sync "$WORK/logs" "$S3/runs/$RUN_ID/logs" --only-show-errors || true
}

stage() {  # name, then the command
  local name="$1"; shift
  if [[ -e "$WORK/.done/$name" ]]; then echo "$name: done before"; return 0; fi
  echo "$name: start $(date -u +%H:%M:%S)"
  local started=$SECONDS status=0 attempt
  for attempt in 1 2; do  # one retry: every stage picks up where it stopped
    set +e
    ( set -e; "$@" ) > "$WORK/logs/$name.log" 2>&1
    status=$?
    set -e
    [[ $status -ne 0 ]] || break
    cp "$WORK/logs/$name.log" "$WORK/logs/$name.attempt$attempt.log"
  done
  aws s3 cp "$WORK/logs/$name.log" "$S3/runs/$RUN_ID/logs/$name.log" --only-show-errors || true
  if [[ $status -ne 0 ]]; then
    progress "$name FAILED after $((SECONDS - started))s: $(tail -n 4 "$WORK/logs/$name.log" | tr '\n' ' ' | cut -c1-400)"
    exit 1
  fi
  touch "$WORK/.done/$name"
  progress "$name done in $((SECONDS - started))s. $(tail -n 1 "$WORK/logs/$name.log" | cut -c1-400)"
  sync_state
}

resume_from_s3() {
  aws s3 ls "$STATE/.done/" >/dev/null 2>&1 || return 0
  echo "resuming $RUN_ID from S3"
  aws s3 sync "$STATE/.done" "$WORK/.done" --only-show-errors
  aws s3 sync "$STATE/runs" "$DET_RUNS" --only-show-errors
  aws s3 sync "$S3/runs/$RUN_ID/report" "$DET_REPORT" --only-show-errors
  aws s3 cp "$S3/runs/$RUN_ID/progress.md" "$WORK/progress.md" --only-show-errors || true
  rm -f "$WORK/.done/setup"                        # the software is per disk
  if [[ -e "$WORK/.done/scan" ]]; then             # the prepared set stands in for the archives
    aws s3 cp "$STATE/data.tar" - | tar -C "$WORK" -xf -
  else
    rm -f "$WORK/.done/fetch" "$WORK/.done/prepare" "$WORK/.done/scan"
  fi
  progress "resumed on a new instance from S3"
}

setup() {
  "$PYTHON" -m pip install -q loguru thop tabulate pycocotools tensorboard opencv-python-headless \
    psutil ninja onnx onnxruntime scipy
  if [[ ! -d "$YOLOX/.git" ]]; then
    # YOLOX: Apache-2.0, as this repo's MIT licence needs (Ultralytics YOLO is AGPL).
    git clone -q https://github.com/Megvii-BaseDetection/YOLOX "$YOLOX"
    git -C "$YOLOX" checkout -q "${YOLOX_COMMIT:-main}"
  fi
  mkdir -p /opt/ml/weights
  for name in yolox_tiny yolox_s; do
    [[ -s "/opt/ml/weights/$name.pth" ]] || curl -fsSL --retry 5 \
      -o "/opt/ml/weights/$name.pth" "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/$name.pth"
  done
  "$PYTHON" -c "import torch, yolox, cv2, numpy; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0), 'numpy', numpy.__version__, 'yolox', yolox.__version__, 'at', '$(git -C "$YOLOX" rev-parse --short HEAD)')"
}

fetch() {
  # The archives are this bucket's own mirrors (fetched and hashed on 7 Oct 2026).
  for name in $ML_DATASETS; do
    mkdir -p "$DET_RAW/$name"
    aws s3 sync "$S3/datasets/$name/raw" "$DET_RAW/$name" --only-show-errors
  done
  if [[ ! -d "$DET_RAW/rad-bengaluru/files/images" ]]; then
    unzip -q "$DET_RAW/rad-bengaluru/rad-v3.zip" 'images/*' -d "$DET_RAW/rad-bengaluru/files"
  fi
  # The owner's drive frames and the screen's manifest (gpt-5-mini's verdict per frame).
  # Private: they stay inside this account.
  mkdir -p "$DET_OWNER"
  aws s3 sync "$S3/v1-work/frames" "$DET_OWNER/frames" --only-show-errors --exclude "*" --include "desktop-*" --include "downloads-*"
  aws s3 sync "$S3/v1-work/owner" "$DET_OWNER/owner" --only-show-errors
  aws s3 cp "$S3/labels/manifest.jsonl" "$DET_OWNER/manifest.jsonl" --only-show-errors
  echo "fetched: $(du -sh "$DET_RAW" | cut -f1) of archives, $(find "$DET_OWNER/frames" -name '*.jpg' | wc -l) owner frames"
}

prepare() {
  "$PYTHON" prepare.py ${PREPARE_ARGS:-}
  aws s3 sync "$DET_DATA/debug" "$S3/runs/$RUN_ID/debug" --only-show-errors
  aws s3 cp "$DET_DATA/stats.json" "$S3/runs/$RUN_ID/report/data-stats.json" --only-show-errors
  "$PYTHON" -c "import json; print('prepared:', json.load(open('$DET_DATA/stats.json'))['totals'])"
}

scan() {
  "$PYTHON" scan_overlays.py
  aws s3 sync "$DET_DATA/debug" "$S3/runs/$RUN_ID/debug" --only-show-errors
  aws s3 cp "$DET_DATA/scan.json" "$S3/runs/$RUN_ID/report/overlay-scan.json" --only-show-errors
  # What a relaunched instance needs instead of the archives.
  tar -C "$WORK" --exclude data/debug -cf - data | aws s3 cp - "$STATE/data.tar" --only-show-errors
  "$PYTHON" -c "import json; d=json.load(open('$DET_DATA/scan.json')); print('scan:', d['scanned'], 'pictures,', d['flagged'], 'look drawn on, removed', d['removed_from'])"
}

train() {  # exp name, pretrained weights
  local name="$1" weights="$2" resume=()
  [[ -s "$DET_RUNS/$name/latest_ckpt.pth" ]] && resume=(--resume)
  # Checkpoints go to S3 every 10 minutes, so a reclaimed instance loses little.
  ( while sleep 600; do sync_state; done ) &
  local syncer=$!
  trap "kill $syncer 2>/dev/null || true" EXIT   # this stage's own subshell
  (cd "$YOLOX" && "$PYTHON" tools/train.py -f "$DETECTOR/exps/$name.py" -d 1 -b "$BATCH" --fp16 \
    -c "/opt/ml/weights/$weights.pth" "${resume[@]}")
  kill $syncer 2>/dev/null || true
  # YOLOX's tools catch their own exceptions and still exit 0 (the trial of 9 Oct 2026
  # "passed" training with no evaluation and no best checkpoint). The outputs are the proof.
  local epochs="${DET_EPOCHS:-45}"
  grep -q "epoch: $epochs/$epochs" "$DET_RUNS/$name/train_log.txt" || { echo "$name: the last epoch was never reached"; return 1; }
  [[ -s "$DET_RUNS/$name/best_ckpt.pth" ]] || { echo "$name: no best checkpoint was saved"; return 1; }
  ! grep -q "Traceback\|RuntimeError" "$DET_RUNS/$name/train_log.txt" || { echo "$name: the training log holds an exception"; grep -n "Error" "$DET_RUNS/$name/train_log.txt" | tail -n 3; return 1; }
  echo "$name: $(grep -E "Average Precision  \(AP\) @\[ IoU=0.50:0.95 \| area=   all" "$DET_RUNS/$name/train_log.txt" | tail -n 1 | tr -s ' ' | cut -c1-120); $(grep -o "best AP is [0-9.]*" "$DET_RUNS/$name/train_log.txt" | tail -n 1)"
}

evaluate() {
  "$PYTHON" evaluate.py --models $MODELS
  aws s3 sync "$DET_REPORT" "$S3/runs/$RUN_ID/report" --only-show-errors
}

export_models() {
  mkdir -p "$WORK/export"
  for name in $MODELS; do
    (cd "$YOLOX" && "$PYTHON" tools/export_onnx.py --output-name "$WORK/export/$name.onnx" \
      -f "$DETECTOR/exps/$name.py" -c "$DET_RUNS/$name/best_ckpt.pth" --decode_in_inference --no-onnxsim -o 13)
    [[ -s "$WORK/export/$name.onnx" ]] || { echo "$name: no ONNX file was written"; return 1; }
    "$PYTHON" - "$WORK/export/$name.onnx" <<'PY'
import sys, time
import numpy as np
import onnxruntime as ort
options = ort.SessionOptions(); options.intra_op_num_threads = 2
session = ort.InferenceSession(sys.argv[1], options, providers=["CPUExecutionProvider"])
feed = {session.get_inputs()[0].name: np.zeros((1, 3, 640, 640), np.float32)}
times = []
for _ in range(12):
    started = time.perf_counter(); out = session.run(None, feed); times.append((time.perf_counter() - started) * 1000)
print(sys.argv[1].rsplit("/", 1)[1], "ONNX Runtime, 2 threads, 640 px:", round(sorted(times[2:])[5], 1), "ms; output", out[0].shape)
PY
    aws s3 cp "$WORK/export/$name.onnx" "$S3/models/$RUN_ID/$name.onnx" --only-show-errors
  done
  aws s3 cp "$DET_REPORT/report.json" "$S3/models/$RUN_ID/report.json" --only-show-errors
}

[[ -n "$(ls -A "$WORK/.done")" ]] || resume_from_s3
for name in ${STAGES:-setup fetch prepare scan train_tiny train_s evaluate export}; do
  case "$name" in
    train_tiny) stage train_tiny train pothole_tiny yolox_tiny ;;
    train_s) stage train_s train pothole_s yolox_s ;;
    export) stage export export_models ;;
    *) stage "$name" "$name" ;;
  esac
done
progress "RUN COMPLETE: ${STAGES:-all stages}"
sync_state
