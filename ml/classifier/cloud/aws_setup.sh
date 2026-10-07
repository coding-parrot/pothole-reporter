#!/usr/bin/env bash
# Create (or confirm) the three things that outlive a training run: the private bucket,
# the instance role and the security group. Idempotent. It touches nothing else in the
# account: not the central stack, not the screen Lambda, not the detector secret.
set -euo pipefail
. "$(dirname "$0")/env.sh"

if ! aws s3api head-bucket --bucket "$ML_BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$ML_BUCKET" \
    --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$ML_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$ML_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-tagging --bucket "$ML_BUCKET" --tagging "TagSet=[{Key=project,Value=$ML_TAG}]"
# A lifecycle filter is a literal prefix, so "datasets/*/raw/" is one rule per dataset.
# Raw archives can be fetched again; nothing else in the bucket ever expires.
RULES=""
for name in $ML_DATASETS; do
  RULES="$RULES{\"ID\":\"expire-raw-$name\",\"Status\":\"Enabled\",\"Filter\":{\"Prefix\":\"datasets/$name/raw/\"},\"Expiration\":{\"Days\":30},\"AbortIncompleteMultipartUpload\":{\"DaysAfterInitiation\":7}},"
done
aws s3api put-bucket-lifecycle-configuration --bucket "$ML_BUCKET" \
  --lifecycle-configuration "{\"Rules\":[${RULES%,}]}"

if ! aws iam get-role --role-name "$ML_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ML_ROLE" --tags "Key=project,Value=$ML_TAG" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam attach-role-policy --role-name "$ML_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
# This bucket, and one read-only secret (the teacher's OpenAI key). Nothing else.
aws iam put-role-policy --role-name "$ML_ROLE" --policy-name ml-bucket-and-teacher-key --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\",\"s3:GetBucketLocation\",\"s3:ListBucketMultipartUploads\"],
     \"Resource\":\"arn:aws:s3:::$ML_BUCKET\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\",\"s3:AbortMultipartUpload\",\"s3:ListMultipartUploadParts\"],
     \"Resource\":\"arn:aws:s3:::$ML_BUCKET/*\"},
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$DETECTOR_SECRET_ARN\"}]}"
if ! aws iam get-instance-profile --instance-profile-name "$ML_ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ML_ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ML_ROLE" --role-name "$ML_ROLE"
fi

VPC="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
GROUP_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$ML_GROUP" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ "$GROUP_ID" == "None" || -z "$GROUP_ID" ]]; then
  GROUP_ID="$(aws ec2 create-security-group --group-name "$ML_GROUP" --vpc-id "$VPC" \
    --description "Pothole ML trainer: no inbound, driven through SSM" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=project,Value=$ML_TAG},{Key=Name,Value=$ML_GROUP}]" \
    --query GroupId --output text)"
fi
INBOUND="$(aws ec2 describe-security-groups --group-ids "$GROUP_ID" --query 'length(SecurityGroups[0].IpPermissions)' --output text)"
[[ "$INBOUND" == "0" ]] || { echo "security group $GROUP_ID has inbound rules; refusing" >&2; exit 1; }
echo "bucket s3://$ML_BUCKET  role $ML_ROLE  security group $GROUP_ID (no inbound) in $VPC"
