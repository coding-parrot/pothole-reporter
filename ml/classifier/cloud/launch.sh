#!/usr/bin/env bash
# Start the one training instance. It can only end itself: it shuts down 12 hours after
# boot whatever happens, and shutting down terminates it and deletes its disk. No key
# pair, no inbound port: it is driven through SSM (cloud/ssm.sh).
set -euo pipefail
. "$(dirname "$0")/env.sh"

RUNNING="$(aws ec2 describe-instances --filters "Name=tag:project,Values=$ML_TAG" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
[[ -z "$RUNNING" ]] || { echo "an instance already exists: $RUNNING (one at a time)" >&2; exit 1; }

AMI="$(aws ssm get-parameter --name "$ML_AMI_PARAMETER" --query Parameter.Value --output text)"
ROOT_DEVICE="$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].RootDeviceName' --output text)"
VPC="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
GROUP_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$ML_GROUP" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)"
USER_DATA="$(mktemp)"
trap 'rm -f "$USER_DATA"' EXIT
printf '#!/bin/bash\nshutdown -h +720\n' > "$USER_DATA"

for zone in "${AWS_REGION}a" "${AWS_REGION}b"; do
  SUBNET="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC" "Name=availability-zone,Values=$zone" \
    "Name=default-for-az,Values=true" --query 'Subnets[0].SubnetId' --output text)"
  [[ "$SUBNET" != "None" ]] || continue
  if INSTANCE="$(aws ec2 run-instances --image-id "$AMI" --instance-type "$ML_INSTANCE_TYPE" \
      --subnet-id "$SUBNET" --security-group-ids "$GROUP_ID" \
      --iam-instance-profile "Name=$ML_ROLE" \
      --instance-initiated-shutdown-behavior terminate \
      --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
      --block-device-mappings "[{\"DeviceName\":\"$ROOT_DEVICE\",\"Ebs\":{\"VolumeSize\":200,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
      --user-data "file://$USER_DATA" \
      --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=$ML_INSTANCE_NAME},{Key=project,Value=$ML_TAG}]" \
        "ResourceType=volume,Tags=[{Key=Name,Value=$ML_INSTANCE_NAME},{Key=project,Value=$ML_TAG}]" \
      --query 'Instances[0].InstanceId' --output text 2>/tmp/ml-launch.err)"; then
    echo "launched $INSTANCE ($ML_INSTANCE_TYPE, $AMI) in $zone at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    aws ec2 wait instance-running --instance-ids "$INSTANCE"
    # The SSM agent registers a minute or so after boot.
    for attempt in $(seq 1 40); do
      PING="$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
        --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
      [[ "$PING" == "Online" ]] && { echo "SSM online"; exit 0; }
      sleep 10
    done
    echo "instance is running but SSM did not come online" >&2; exit 1
  fi
  cat /tmp/ml-launch.err >&2
done
echo "could not launch in any zone" >&2; exit 1
