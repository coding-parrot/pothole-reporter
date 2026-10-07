#!/usr/bin/env bash
# Run the service tests, the slow Python suites and the full harness on AWS CodeBuild.
#
#   tools/cloud/ci.sh [ref]
#
# ref is a branch, tag or commit; the default is the current HEAD. Either must already
# be on origin, because CodeBuild clones the public repository: the script refuses an
# unpushed commit and says so. It starts one build of project pothole-reporter-ci
# (ap-south-1, on demand), waits (35 minutes at most, then the build is stopped), prints
# the harness summary (passed, failed, known-failing) and the names of any new failures,
# and exits non-zero on a regression against tools/harness/baseline.json.
# Results and logs: s3://pothole-reporter-ml-695656921622-ap-south-1/ci/<ref>/<build id>/
#
# Cost: one build of about 10 minutes on the smallest instance (2 vCPU) at USD 0.005 a
# minute: about USD 0.05.
#
# Other forms:
#   ci.sh [ref] --no-wait        start and return; prints the build id
#   ci.sh --status <build id>    one status line
#   ci.sh --collect <build id>   the result of a finished build
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-pothole}"
exec python3 "$(dirname "$0")/ci.py" "$@"
