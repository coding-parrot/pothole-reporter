# Names shared by every cloud script. Source it: `. ml/classifier/cloud/env.sh`.
export AWS_PROFILE="${AWS_PROFILE:-pothole}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
ACCOUNT_ID=695656921622
ML_BUCKET="pothole-reporter-ml-${ACCOUNT_ID}-${AWS_REGION}"
ML_ROLE="pothole-reporter-ml-trainer"
ML_GROUP="pothole-reporter-ml-trainer"
ML_TAG="pothole-reporter-ml"
ML_INSTANCE_NAME="pothole-ml-trainer"
ML_INSTANCE_TYPE="g4dn.2xlarge"
DETECTOR_SECRET_ARN="arn:aws:secretsmanager:${AWS_REGION}:${ACCOUNT_ID}:secret:pothole-reporter-central/detector-G2AzBg"
# The current AWS Deep Learning AMI with PyTorch for Ubuntu 22.04, resolved at launch.
ML_AMI_PARAMETER="/aws/service/deeplearning/ami/x86_64/oss-nvidia-driver-gpu-pytorch-2.7-ubuntu-22.04/latest/ami-id"
ML_REPO="https://github.com/coding-parrot/pothole-reporter.git"
ML_BRANCH="feat/screen-v2"
# Every dataset whose raw archive is mirrored under datasets/<name>/raw/ (30-day expiry).
ML_DATASETS="rdd2022 rad-bengaluru irdd-iraq bucko-dashcam cracks-potholes-brazil attain-iran rome-road-damage bharatpothole road-damage-alvarobasily"
