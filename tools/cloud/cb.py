"""Shared by ci.py and eval_prompt_launch.py: start a CodeBuild build, wait with a
limit, fetch results from S3. Standard library plus the aws CLI."""
import json
import math
import os
import subprocess
import sys
import time

REGION = "ap-south-1"
PROJECT = "pothole-reporter-ci"
BUCKET = "pothole-reporter-ml-695656921622-ap-south-1"
# On-demand Linux price per build minute in ap-south-1 (AWS CodeBuild pricing page,
# October 2026). Used only to print what a run cost.
USD_PER_MINUTE = {"BUILD_GENERAL1_SMALL": 0.005, "BUILD_GENERAL1_MEDIUM": 0.01,
                  "BUILD_GENERAL1_LARGE": 0.02}


def aws(*args, timeout=120, check=True):
    os.environ.setdefault("AWS_PROFILE", "pothole")
    done = subprocess.run(["aws", *args, "--region", REGION, "--output", "json"],
                          capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        if not check:
            return None
        sys.exit("aws %s failed: %s" % (" ".join(args[:2]), done.stderr.strip()[:600]))
    return json.loads(done.stdout) if done.stdout.strip() else {}


def git(*args):
    here = os.path.dirname(os.path.abspath(__file__))
    done = subprocess.run(["git", "-C", here, *args], capture_output=True, text=True)
    return done.returncode, done.stdout.strip()


def pushed_commit(ref):
    """The commit a ref names, after checking origin has it. Exits with a clear message."""
    code, sha = git("rev-parse", "--verify", "--quiet", "%s^{commit}" % ref)
    if code != 0:
        sys.exit("%s is not a commit in this checkout" % ref)
    git("fetch", "--quiet", "origin")
    code, remotes = git("branch", "-r", "--contains", sha)
    if code != 0 or not remotes:
        sys.exit("%s (%s) is not on origin. CodeBuild clones from GitHub, so push it first:\n"
                 "  git push origin HEAD" % (ref, sha[:10]))
    return sha


def ref_key(ref):
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in ref)


def build(build_id):
    return aws("codebuild", "batch-get-builds", "--ids", build_id)["builds"][0]


def wait(build_id, limit_minutes):
    deadline = time.time() + limit_minutes * 60
    last = None
    while True:
        current = build(build_id)
        line = "%s %s" % (current["buildStatus"], current.get("currentPhase", ""))
        if line != last:
            print("   %s %s" % (time.strftime("%H:%M:%S"), line), flush=True)
            last = line
        if current.get("buildComplete"):
            return current
        if time.time() > deadline:
            aws("codebuild", "stop-build", "--id", build_id)
            sys.exit("the build did not finish in %d minutes; it has been stopped (%s)"
                     % (limit_minutes, build_id))
        time.sleep(20)


def minutes_and_cost(current):
    """Billed minutes (CodeBuild rounds each build up to a whole minute) and USD."""
    if "endTime" not in current:
        return 0, 0.0
    phases = [p for p in current.get("phases", []) if p.get("phaseType") not in ("SUBMITTED", "QUEUED", "COMPLETED")]
    seconds = sum(p.get("durationInSeconds", 0) for p in phases)
    minutes = max(1, math.ceil(seconds / 60))
    rate = USD_PER_MINUTE.get(current["environment"]["computeType"], 0.01)
    return minutes, minutes * rate


def s3_text(key):
    done = subprocess.run(["aws", "s3", "cp", "s3://%s/%s" % (BUCKET, key), "-", "--region", REGION],
                          capture_output=True, text=True, timeout=120)
    return done.stdout if done.returncode == 0 else None


def log_tail(current, lines=40):
    logs = current.get("logs", {})
    if not logs.get("groupName") or not logs.get("streamName"):
        return ""
    events = aws("logs", "get-log-events", "--log-group-name", logs["groupName"],
                 "--log-stream-name", logs["streamName"], "--limit", str(lines),
                 "--no-start-from-head", check=False)
    return "".join(event["message"] for event in (events or {}).get("events", []))
