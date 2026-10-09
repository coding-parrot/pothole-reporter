# Names for the detector run. Source it: `. ml/detector/cloud/env.sh`.
# The bucket, role, security group and the one-instance rule are the screen's
# (ml/classifier/cloud/env.sh); only what differs is set here.
export ML_INSTANCE_TYPE="${DET_INSTANCE_TYPE:-g5.2xlarge}"
export ML_BRANCH="${DET_BRANCH:-feat/pothole-detector}"
. "$(dirname "${BASH_SOURCE[0]}")/../../classifier/cloud/env.sh"
export RUN_ID="${RUN_ID:-pothole-det-20261009}"
