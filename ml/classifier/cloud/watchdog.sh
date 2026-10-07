#!/usr/bin/env bash
# No idle GPU. Powers the instance off (which terminates it and deletes its disk) the
# moment the last stage is done, or when no driver has been running for 10 minutes: a
# stage that failed and was not restarted, or a session that went away. The state a
# relaunch needs is synced to S3 first.
#
#   nohup setsid ml/classifier/cloud/watchdog.sh > /opt/ml/watchdog.log 2>&1 &
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/../work"
LAST_STAGE="${LAST_STAGE:-export}"
IDLE_LIMIT="${IDLE_LIMIT:-600}"
idle=0
while :; do
  if pgrep -f "cloud/run.sh" >/dev/null; then
    idle=0
  elif [[ -e "$WORK/.done/$LAST_STAGE" ]]; then
    reason="run complete"; break
  else
    idle=$((idle + 20))
    [[ $idle -lt $IDLE_LIMIT ]] || { reason="no driver for ${IDLE_LIMIT}s"; break; }
  fi
  sleep 20
done
echo "- $(date -u +%Y-%m-%dT%H:%M:%SZ) watchdog: $reason; syncing state and powering off" >> "$WORK/progress.md"
"$HERE/sync_state.sh"
shutdown -h now
