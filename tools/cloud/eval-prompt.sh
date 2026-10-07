#!/usr/bin/env bash
# Prompt A (production) against prompt B (a candidate) on N labelled frames, on AWS.
#
#   tools/cloud/eval-prompt.sh --candidate <file> --frames N --budget-usd X
#   tools/cloud/eval-prompt.sh --candidate production --frames 60 --budget-usd 0.10
#
# The candidate file holds the base detection prompt (what llm/prompts/detection.mjs
# calls base); the drive capture line is appended to both arms as production does. The
# word production runs the production prompt against itself, which shows how much two
# runs of the same prompt disagree: the noise floor any real difference has to beat.
#
# Runs as one CodeBuild build (project pothole-reporter-ci, smallest instance, with the
# eval role) at the current HEAD, which must be pushed. Frames and labels come from
# s3://pothole-reporter-ml-695656921622-ap-south-1/v1-work/ (manifest.jsonl, frames/),
# half labelled damaged and half undamaged, test split first, fixed by --seed. The
# OpenAI key is read from Secrets Manager (pothole-reporter-central/detector) inside
# the build and never leaves it. Calls go over HTTP/1.1 keep-alive.
#
# Reports damaged kept, undamaged kept, discordant pairs with an exact two-sided test,
# output tokens and OpenAI's processing time (openai-processing-ms, never wall time).
# Spend is added up from the usage fields and no call starts unless the budget covers
# it; a run stopped by the budget exits 3. Results: .../evals/<run id>/
#
# Cost: about USD 0.001 per frame at gpt-5-mini list price (two calls) plus two or three
# CodeBuild minutes at USD 0.005.
#
# Other forms: --no-wait, --status <build id>, --collect <build id>.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-pothole}"
exec python3 "$(dirname "$0")/eval_prompt.py" "$@"
