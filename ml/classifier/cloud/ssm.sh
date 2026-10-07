#!/usr/bin/env bash
# Run one shell command on the training instance through SSM and print what it printed.
#
#   cloud/ssm.sh 'nvidia-smi'            # waits up to 10 minutes
#   TIMEOUT=3600 cloud/ssm.sh '...'
#
# SSM returns at most 24,000 characters of output; long jobs write a log and are tailed.
set -euo pipefail
. "$(dirname "$0")/env.sh"
TIMEOUT="${TIMEOUT:-600}"
INSTANCE="$(aws ec2 describe-instances --filters "Name=tag:project,Values=$ML_TAG" \
  "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].InstanceId' --output text)"
[[ "$INSTANCE" != "None" && -n "$INSTANCE" ]] || { echo "no running trainer instance" >&2; exit 1; }
PARAMETERS="$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]],"executionTimeout":[sys.argv[2]]}))' "$1" "$TIMEOUT")"
COMMAND="$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --timeout-seconds 60 --parameters "$PARAMETERS" --query Command.CommandId --output text)"
DEADLINE=$(( $(date +%s) + TIMEOUT + 30 ))
while :; do
  STATUS="$(aws ssm get-command-invocation --command-id "$COMMAND" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$STATUS" in Pending|InProgress|Delayed) ;; *) break ;; esac
  [[ $(date +%s) -lt $DEADLINE ]] || { echo "gave up waiting for $COMMAND" >&2; exit 1; }
  sleep 3
done
aws ssm get-command-invocation --command-id "$COMMAND" --instance-id "$INSTANCE" \
  --query StandardOutputContent --output text
ERRORS="$(aws ssm get-command-invocation --command-id "$COMMAND" --instance-id "$INSTANCE" \
  --query StandardErrorContent --output text)"
[[ -z "$ERRORS" || "$ERRORS" == "None" ]] || echo "--- stderr ---
$ERRORS" >&2
[[ "$STATUS" == "Success" ]] || { echo "command ended: $STATUS" >&2; exit 1; }
