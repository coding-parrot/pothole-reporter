#!/usr/bin/env bash
# Copy everything a relaunched instance would need to carry on, to
# s3://$ML_BUCKET/runs/$RUN_ID/state/: stage markers, embeddings, heads, scores,
# fine-tuned weights and their per-epoch checkpoints, results. Frames, labels and the
# manifest are already on S3 from their own stages.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
unset AWS_PROFILE
WORK="$HERE/../work"
RUN_ID="${RUN_ID:-screen-v2-20261007}"
STATE="s3://$ML_BUCKET/runs/$RUN_ID/state"
for part in .done embeddings heads scores finetuned results report release onnx; do
  [[ -d "$WORK/$part" ]] && aws s3 sync "$WORK/$part" "$STATE/$part" --only-show-errors
done
[[ -d "$WORK/logs" ]] && aws s3 sync "$WORK/logs" "s3://$ML_BUCKET/runs/$RUN_ID/logs" --only-show-errors
[[ -f "$WORK/progress.md" ]] && aws s3 cp "$WORK/progress.md" "s3://$ML_BUCKET/runs/$RUN_ID/progress.md" --only-show-errors
true
