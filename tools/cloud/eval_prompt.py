#!/usr/bin/env python3
"""Compare the production detection prompt with a candidate on labelled frames, on AWS.
Called by tools/cloud/eval-prompt.sh; see its header."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import cb

EVAL_ROLE = "arn:aws:iam::695656921622:role/pothole-reporter-eval-codebuild"
BUILDSPEC = "tools/cloud/buildspec-eval.yml"


def start(options):
    sha = cb.pushed_commit("HEAD")
    code, _ = cb.git("cat-file", "-e", "%s:%s" % (sha, BUILDSPEC))
    if code != 0:
        sys.exit("HEAD has no %s" % BUILDSPEC)
    production = options.candidate == "production"
    if not production and not os.path.isfile(options.candidate):
        sys.exit("no candidate prompt file at %s (or pass the word production)" % options.candidate)
    text = b"" if production else open(options.candidate, "rb").read()
    if not production and not text.strip():
        sys.exit("the candidate prompt file is empty")
    run_id = "prompt-%s-%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
                               "production" if production else hashlib.sha256(text).hexdigest()[:8])
    variables = {"EVAL_RUN_ID": run_id, "EVAL_FRAMES": str(options.frames),
                 "EVAL_BUDGET_USD": str(options.budget_usd), "EVAL_SEED": str(options.seed)}
    if not production:
        key = "evals/%s/candidate.txt" % run_id
        put = subprocess.run(["aws", "s3", "cp", options.candidate, "s3://%s/%s" % (cb.BUCKET, key),
                              "--region", cb.REGION, "--only-show-errors"], capture_output=True, text=True)
        if put.returncode != 0:
            sys.exit("could not upload the candidate prompt: %s" % put.stderr.strip()[:300])
        variables.update(EVAL_CANDIDATE_KEY=key, EVAL_CANDIDATE_NAME=os.path.basename(options.candidate))
    started = cb.aws(
        "codebuild", "start-build", "--project-name", cb.PROJECT, "--source-version", sha,
        "--buildspec-override", BUILDSPEC, "--service-role-override", EVAL_ROLE,
        "--compute-type-override", "BUILD_GENERAL1_SMALL", "--timeout-in-minutes-override", "30",
        "--environment-variables-override",
        json.dumps([{"name": k, "value": v, "type": "PLAINTEXT"} for k, v in variables.items()]))["build"]
    print("1/3 eval %s started at %s: %s" % (run_id, sha[:10], started["id"]))
    print("   %d frames, budget USD %s, candidate %s" % (options.frames, options.budget_usd, options.candidate))
    return started["id"]


def report(build_id):
    current = cb.build(build_id)
    if not current.get("buildComplete"):
        print("the build is still %s (%s)" % (current["buildStatus"], current.get("currentPhase")))
        return 2
    run_id = next((v["value"] for v in current["environment"].get("environmentVariables", [])
                   if v["name"] == "EVAL_RUN_ID"), "")
    minutes, cost = cb.minutes_and_cost(current)
    print("3/3 eval %s: build %s" % (run_id, current["buildStatus"]))
    text = cb.s3_text("evals/%s/summary.txt" % run_id)
    if not text:
        print("FAIL no summary was written; the end of the build log:")
        print(cb.log_tail(current))
        print("build minutes: %d, about USD %.3f" % (minutes, cost))
        return 1
    summary = json.loads(cb.s3_text("evals/%s/summary.json" % run_id) or "{}")
    print("\n" + text)
    print("CodeBuild: %d billed minutes on %s, about USD %.3f" % (
        minutes, current["environment"]["computeType"], cost))
    print("results: s3://%s/evals/%s/" % (cb.BUCKET, run_id))
    if summary.get("stopped_on_budget"):
        return 3
    return 0 if current["buildStatus"] == "SUCCEEDED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--candidate", help="file holding the candidate base prompt, or the word "
                        "production to run the production prompt against itself (the noise floor)")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--budget-usd", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--wait-minutes", type=int, default=30)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--status", metavar="BUILD_ID")
    parser.add_argument("--collect", metavar="BUILD_ID")
    options = parser.parse_args()
    if options.status:
        current = cb.build(options.status)
        print(current["buildStatus"], current.get("currentPhase", ""))
        return 0
    if options.collect:
        return report(options.collect)
    if not options.candidate:
        parser.error("--candidate is required")
    if options.frames < 2 or options.budget_usd <= 0:
        parser.error("--frames must be at least 2 and --budget-usd above 0")
    build_id = start(options)
    if options.no_wait:
        return 0
    print("2/3 waiting (limit %d minutes)" % options.wait_minutes)
    cb.wait(build_id, options.wait_minutes)
    return report(build_id)


if __name__ == "__main__":
    sys.exit(main())
