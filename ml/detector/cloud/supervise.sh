#!/usr/bin/env bash
# From the Mac: keep the detector run going until it says RUN COMPLETE or FAILED.
#
#   ml/detector/cloud/supervise.sh            # start (or adopt) the run and watch it
#
# It asks for a spot instance (about a third of the on-demand price); when AWS takes
# one back the instance simply disappears, and this starts another, which restores the
# finished stages and the latest checkpoint from S3. It only polls: all the work is on
# the instance. Ends by itself after MAX_HOURS (10), at most 4 launches.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
CLOUD="$HERE/../../classifier/cloud"
DEADLINE=$(( $(date +%s) + ${MAX_HOURS:-10} * 3600 ))
launches=0

progress() { aws s3 cp "s3://$ML_BUCKET/runs/$RUN_ID/progress.md" - 2>/dev/null; }
instance() {
  aws ec2 describe-instances --filters "Name=tag:project,Values=$ML_TAG" \
    "Name=instance-state-name,Values=pending,running" --query 'Reservations[].Instances[].InstanceId' --output text
}
start() {
  MARKET="${MARKET:-spot}" "$CLOUD/launch.sh" || return 1
  "$CLOUD/ssm.sh" "mkdir -p /opt/ml && cd /opt/ml && rm -rf repo && git clone -q -b $ML_BRANCH $ML_REPO repo && cd repo \
    && (RUN_ID=$RUN_ID nohup setsid ml/detector/cloud/run.sh > /opt/ml/run.log 2>&1 &) \
    && (nohup setsid ml/detector/cloud/watchdog.sh > /opt/ml/watchdog.log 2>&1 &) && sleep 3 && pgrep -f detector/cloud/run.sh | head -n 1"
}

while [[ $(date +%s) -lt $DEADLINE ]]; do
  log="$(progress)"
  if grep -q "RUN COMPLETE" <<<"$log"; then echo "$log" | tail -n 12; exit 0; fi
  if tail -n 1 <<<"$log" | grep -q "FAILED"; then echo "$log" | tail -n 6; exit 2; fi
  if [[ -z "$(instance)" ]]; then
    [[ $launches -lt 4 ]] || { echo "four launches used; stopping"; echo "$log" | tail -n 6; exit 3; }
    launches=$((launches + 1))
    echo "$(date -u +%H:%M:%SZ) launch $launches"
    start || echo "launch $launches did not start; trying again in two minutes"
  fi
  sleep 120
done
echo "still running after ${MAX_HOURS:-10} hours; stopping the watch (the instance ends itself at 12)"; progress | tail -n 6; exit 4
