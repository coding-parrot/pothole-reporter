#!/usr/bin/env bash
# Terminate the training instance and prove nothing tagged for this project is left
# running or unattached. The bucket, the role and the security group stay.
set -euo pipefail
. "$(dirname "$0")/env.sh"
IDS="$(aws ec2 describe-instances --filters "Name=tag:project,Values=$ML_TAG" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [[ -n "$IDS" ]]; then
  aws ec2 terminate-instances --instance-ids $IDS --query 'TerminatingInstances[].[InstanceId,CurrentState.Name]' --output text
  aws ec2 wait instance-terminated --instance-ids $IDS
fi
echo "instances tagged project=$ML_TAG:"
aws ec2 describe-instances --filters "Name=tag:project,Values=$ML_TAG" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,StateTransitionReason]' --output text
echo "volumes tagged project=$ML_TAG (none expected):"
aws ec2 describe-volumes --filters "Name=tag:project,Values=$ML_TAG" \
  --query 'Volumes[].[VolumeId,State,Size]' --output text
