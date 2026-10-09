#!/usr/bin/env bash
# Create or update the screen Lambda: pothole-reporter-central-screen, its role and its
# log group. It touches nothing else: not the central stack, not the detector secret.
#
#   AWS_PROFILE=pothole ml/classifier/lambda/deploy.sh
#
# The screen checks the SHA-256 of the x-yolo-api-key header, like the YOLO Lambda. The
# key itself is generated once into SCREEN_API_KEY_FILE (never printed, never committed)
# and belongs in the detector secret as the field yolo_api_key, which the owner adds.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# PACKAGE_DIR: another Lambda that speaks the same contract and is deployed the same way
# (ml/detector/lambda, the pothole locator). It brings its own build.sh, tests and model.
PACKAGE="${PACKAGE_DIR:-$HERE}"
FUNCTION="${FUNCTION:-pothole-reporter-central-screen}"
DESCRIPTION="${DESCRIPTION:-Encoder-only drive-frame screen (ml/classifier)}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ROLE="${FUNCTION}-role"
LOG_GROUP="/aws/lambda/${FUNCTION}"
# Outside every worktree: the copy that lived in a worktree's work/ directory went when
# that worktree was cleaned up on 9 Oct 2026 (it was restored from the detector secret).
KEY_FILE="${SCREEN_API_KEY_FILE:-$HOME/.config/pothole-reporter/screen-api-key}"
export AWS_PAGER=""

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
BUCKET="${ARTIFACT_BUCKET:-pothole-reporter-central-${ACCOUNT_ID}-${AWS_REGION}}"

(cd "$PACKAGE" && npm test)
"$PACKAGE/build.sh"

if [[ ! -s "$KEY_FILE" ]]; then
  # A new key would lock the central service out of a function it already calls. Only a
  # first deploy of a new function may make one, and it says so.
  if aws lambda get-function --function-name "$FUNCTION" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "$KEY_FILE is missing and $FUNCTION already exists; restore the key (it is the detector secret's yolo_api_key) before deploying" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$KEY_FILE")"
  (umask 077 && openssl rand -hex 32 > "$KEY_FILE")
  echo "generated a new screen key in $KEY_FILE"
fi
KEY_SHA="$(tr -d '\n' < "$KEY_FILE" | shasum -a 256 | cut -d' ' -f1)"

MODEL_VERSION="$(node -e 'console.log(JSON.parse(require("fs").readFileSync(process.argv[1])).model_version)' "$PACKAGE/model/model.json")"
MODEL_SHA="$(shasum -a 256 "$PACKAGE/model/model.onnx" | cut -d' ' -f1)"
# PUBLISH_MODEL=1 records the released ONNX and its metadata under models/ in the
# artifact bucket. Trial deploys leave the bucket alone.
if [[ "${PUBLISH_MODEL:-0}" == "1" ]]; then
  aws s3 cp "$PACKAGE/model/model.onnx" "s3://$BUCKET/models/${MODEL_VERSION}.onnx" --region "$AWS_REGION" \
    --metadata "sha256=$MODEL_SHA" >/dev/null
  aws s3 cp "$PACKAGE/model/model.json" "s3://$BUCKET/models/${MODEL_VERSION}.json" --region "$AWS_REGION" >/dev/null
  echo "model: s3://$BUCKET/models/${MODEL_VERSION}.onnx sha256 $MODEL_SHA"
fi
# Lambda takes a zip of up to 50 MB directly; a larger one has to come from S3.
ZIP="$PACKAGE/build/screen-lambda.zip"
if [[ "$(stat -f%z "$ZIP" 2>/dev/null || stat -c%s "$ZIP")" -lt 48000000 ]]; then
  CODE_CREATE=(--zip-file "fileb://$ZIP")
  CODE_UPDATE=(--zip-file "fileb://$ZIP")
else
  ZIP_KEY="models/screen-lambda-$(shasum -a 256 "$ZIP" | cut -c1-16).zip"
  aws s3 cp "$ZIP" "s3://$BUCKET/$ZIP_KEY" --region "$AWS_REGION" >/dev/null
  CODE_CREATE=(--code "S3Bucket=$BUCKET,S3Key=$ZIP_KEY")
  CODE_UPDATE=(--s3-bucket "$BUCKET" --s3-key "$ZIP_KEY")
fi

aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$AWS_REGION" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$AWS_REGION"

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  sleep 10  # a new role is not assumable by Lambda for a few seconds
fi
# Its own log streams and nothing else: no S3, no secret, no database.
aws iam put-role-policy --role-name "$ROLE" --policy-name write-own-logs --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogStream\",\"logs:PutLogEvents\"],
    \"Resource\":\"arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:${LOG_GROUP}:*\"}]}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE}"
ENVIRONMENT="Variables={API_KEY_SHA256=${KEY_SHA}${SCREEN_THRESHOLD:+,SCREEN_THRESHOLD=${SCREEN_THRESHOLD}}}"

if aws lambda get-function --function-name "$FUNCTION" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" --region "$AWS_REGION" \
    "${CODE_UPDATE[@]}" --architectures arm64 >/dev/null
  aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$AWS_REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --region "$AWS_REGION" \
    --memory-size 2048 --timeout 15 --environment "$ENVIRONMENT" >/dev/null
else
  for attempt in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$FUNCTION" --region "$AWS_REGION" \
      --runtime nodejs22.x --architectures arm64 --handler handler.handler --role "$ROLE_ARN" \
      --memory-size 2048 --timeout 15 "${CODE_CREATE[@]}" \
      --environment "$ENVIRONMENT" \
      --description "$DESCRIPTION" >/dev/null 2>"$PACKAGE/build/create.err"; then
      break
    fi
    grep -q "cannot be assumed" "$PACKAGE/build/create.err" || { cat "$PACKAGE/build/create.err" >&2; exit 1; }
    sleep 5
  done
fi
aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$AWS_REGION"
aws lambda get-function-configuration --function-name "$FUNCTION" --region "$AWS_REGION" \
  --query '{arn:FunctionArn,runtime:Runtime,arch:Architectures[0],memory:MemorySize,timeout:Timeout,codeSize:CodeSize,sha:CodeSha256}' --output table
echo "deployed model: ${MODEL_VERSION} sha256 $MODEL_SHA"
echo "screen key file: $KEY_FILE (its value is the detector secret's yolo_api_key field)"
