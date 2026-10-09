#!/usr/bin/env bash
# No idle GPU. Powers the instance off (which terminates it and deletes its disk) when
# the run is complete, or when no driver has been running for $IDLE_LIMIT seconds.
#
#   nohup setsid ml/detector/cloud/watchdog.sh > /opt/ml/watchdog.log 2>&1 &
set -uo pipefail
WORK="${DET_WORK:-/opt/ml/det}"
IDLE_LIMIT="${IDLE_LIMIT:-900}"
idle=0
while :; do
  if pgrep -f "detector/cloud/run.sh" >/dev/null; then
    idle=0
  elif grep -q "RUN COMPLETE" "$WORK/progress.md" 2>/dev/null; then
    reason="run complete"; break
  else
    idle=$((idle + 20))
    [[ $idle -lt $IDLE_LIMIT ]] || { reason="no driver for ${IDLE_LIMIT}s"; break; }
  fi
  sleep 20
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) watchdog: $reason; powering off"
shutdown -h now
