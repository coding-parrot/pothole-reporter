#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
STACK_NAME="${STACK_NAME:-pothole-reporter-central}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-}"
CODE_KEY="${CODE_KEY:-}"

command -v aws >/dev/null || { echo "AWS CLI is required; install it and run aws login first." >&2; exit 2; }
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
if [[ -z "$ARTIFACT_BUCKET" ]]; then
  ARTIFACT_BUCKET="pothole-reporter-central-${ACCOUNT_ID}-${AWS_REGION}"
fi

cd "$ROOT_DIR"
# The unit tests include the IAM check; a red suite must never reach the stack.
(cd infra/aws-central && npm test)

# What production looked like before this deploy. Informational here (a deploy is how
# a broken rule gets fixed); the same rules fail the scheduled run in CI.
node infra/aws-central/tools/production-health.mjs --window 24h || true

# Caps and the geocoder are decided in template.yaml. CloudFormation keeps a stack's old
# parameter values unless they are passed again, which is how the template said 500 a day
# while the stack still ran 50. Pass every such parameter explicitly from the template.
template_default() {
  sed -n "/^  $1:\$/,/Default:/p" infra/aws-central/template.yaml | sed -n 's/^    Default: //p' | head -1
}
TEMPLATE_PARAMETERS=""
for name in DailyVisionCap GlobalVisionMinuteCap GlobalVisionDailyCap MonthlyVisionCap ReservedConcurrency GeocoderReverseUrl AlertEmail; do
  default_value="$(template_default "$name")"
  [[ -n "$default_value" ]] || { echo "template.yaml declares no default for $name" >&2; exit 1; }
  TEMPLATE_PARAMETERS="$TEMPLATE_PARAMETERS $name=$default_value"
done
npm install --prefix infra/aws-central --omit=dev --no-audit --no-fund
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/package/infra/aws-central" "$TMP_DIR/package/llm/generated" "$TMP_DIR/package/data"
cp -R infra/aws-central/service "$TMP_DIR/package/infra/aws-central/"
cp infra/aws-central/package.json "$TMP_DIR/package/infra/aws-central/"
cp -R infra/aws-central/node_modules "$TMP_DIR/package/infra/aws-central/"
cp llm/generated/contract.mjs "$TMP_DIR/package/llm/generated/"
# Karnataka road ownership, whole: the KGIS town, highway (national, state, district) and
# gram panchayat polygons and the state boundary, with the grid a lookup walks. The
# service answers every Karnataka lookup from it and never calls KGIS. Same path relative
# to the service as in the repo, so geolocation.mjs needs no configuration to find it.
# About 38 MB, 22 MB zipped.
cp data/karnataka-ownership.bin "$TMP_DIR/package/data/"
# Karnataka ward polygons (KGIS Ward New layer, 7,421 wards): what the service names a
# municipal point's ward from, with no live KGIS call. About 6.4 MB, 2.2 MB zipped.
cp data/karnataka-ward-geometry.json "$TMP_DIR/package/data/"
# Street and locality names (OpenStreetMap extract, ODbL): Karnataka and nine cities
# outside it, one directory of hash-checked tiles per region. local-address.mjs reads a
# tile on first need, so the geocoder is asked only where no region has a street. The
# build's working directory (data/streets/.work, 4.4 GB) is never packaged. About 42 MB.
mkdir -p "$TMP_DIR/package/data/streets"
# No trailing slash on the source: BSD cp copies a directory's CONTENTS when its name
# ends in one, which once flattened every region into a single directory.
for region in data/streets/*; do
  [[ -d "$region" ]] && cp -R "$region" "$TMP_DIR/package/data/streets/"
done
# The national tender catalogues (highway contracts, State/UT road notices, PMGSY
# agreements): the manifests the shipped phone names, under fixed names, and the pack
# they pin for every state, hash-checked. national-tenders.mjs reads them lazily per
# state from this same path relative to the service. About 11 MB, 103 files.
node infra/aws-central/tools/stage-national-tenders.mjs "$TMP_DIR/package/data/national-tenders"
# Ward polygons outside Karnataka: data/wards/runtime.json and only the snapshots it
# switches on (Bhopal and Ahmedabad on 7 Oct 2026; the other 26 files and the 9 MB
# gazetteer stay out). india-wards.mjs reads them from this same path relative to the
# service. Each file is checked against the hash the list pins, so a switched-on snapshot
# that is missing or edited stops the deploy here. About 160 KB, 60 KB zipped.
node infra/aws-central/tools/stage-india-wards.mjs "$TMP_DIR/package/data/wards"
# The staged package answers a real point from its own files, network cut, or nothing is
# uploaded.
node infra/aws-central/tools/check-package.mjs "$TMP_DIR/package"
(cd "$TMP_DIR/package" && zip -q -r "$TMP_DIR/central-lambda.zip" infra llm data)
echo "package: $(du -h "$TMP_DIR/central-lambda.zip" | cut -f1) zipped, $(du -sh "$TMP_DIR/package" | cut -f1) unpacked"
# A content-addressed key makes CloudFormation see every code change; a fixed key reports
# "No changes" and leaves the old Lambda code running.
if [[ -z "$CODE_KEY" ]]; then
  CODE_SHA="$( (cd "$TMP_DIR/package" && find infra llm data -type f ! -path '*/node_modules/*' -print0 | sort -z | xargs -0 shasum -a 256) | shasum -a 256 | cut -c1-16)"
  CODE_KEY="releases/central-lambda-${CODE_SHA}.zip"
fi

if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" >/dev/null 2>&1; then
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  aws s3api put-bucket-versioning --bucket "$ARTIFACT_BUCKET" --versioning-configuration Status=Enabled --region "$AWS_REGION" >/dev/null
fi
aws s3 cp "$TMP_DIR/central-lambda.zip" "s3://$ARTIFACT_BUCKET/$CODE_KEY" --region "$AWS_REGION" >/dev/null

aws cloudformation deploy \
  --template-file infra/aws-central/template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides CodeS3Bucket="$ARTIFACT_BUCKET" CodeS3Key="$CODE_KEY" $TEMPLATE_PARAMETERS ${EXTRA_PARAMETER_OVERRIDES:-} \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs' --output table

# Every unit test passed while GET /v1/map returned 500 for ten days, because the fault
# was an IAM grant only the real stack can exercise. Probe the public read routes.
API_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)"
smoke_failed=0
for route in /v1/health /v1/map /v1/impact; do
  status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$API_URL$route" || true)"
  echo "smoke $route $status"
  [[ "$status" == "200" ]] || smoke_failed=1
done
if [[ "$smoke_failed" != "0" ]]; then
  echo "Post-deploy smoke failed; the stack is live with the new code, so roll back or fix now." >&2
  exit 1
fi

# The canary registers an install, runs a real detection and two tender lookups against
# the live stack and fails on any rule a user would feel. A deploy is not done until it
# passes; the previous code key is printed so a rollback is one command away.
echo "previous code key: $(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
  --query "Stacks[0].Parameters[?ParameterKey=='CodeS3Key'].ParameterValue" --output text 2>/dev/null || true)"
# CANARY_CATALOGUE_IS_THIS_CHECKOUT: the notices production now serves are the ones staged
# above, so the canary may hold the Ahmedabad ward to the notices this checkout has for it.
if ! API_URL="$API_URL" CANARY_CATALOGUE_IS_THIS_CHECKOUT=1 node infra/aws-central/tools/production-health.mjs --canary; then
  echo "Post-deploy canary failed; the stack is live with the new code. Fix forward or redeploy the previous code key." >&2
  exit 1
fi
