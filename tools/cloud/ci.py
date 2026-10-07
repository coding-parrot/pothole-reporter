#!/usr/bin/env python3
"""Run the service tests, the slow Python suites and the full harness for one git ref
on AWS CodeBuild, and judge the result against tools/harness/baseline.json.
Called by tools/cloud/ci.sh; see its header."""
import argparse
import json
import os
import sys

import cb

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDSPEC = "tools/cloud/buildspec-ci.yml"


def start(ref, compute):
    if ref:
        name = ref
    else:
        _, name = cb.git("rev-parse", "--abbrev-ref", "HEAD")
        ref = "HEAD"
    sha = cb.pushed_commit(ref)
    arguments = ["codebuild", "start-build", "--project-name", cb.PROJECT, "--source-version", sha,
                 "--environment-variables-override",
                 json.dumps([{"name": "CI_REF", "value": name, "type": "PLAINTEXT"}])]
    code, _ = cb.git("cat-file", "-e", "%s:%s" % (sha, BUILDSPEC))
    if code != 0:
        # A commit from before the buildspec existed: send this checkout's copy.
        print("   %s has no %s; using the one in this checkout" % (sha[:10], BUILDSPEC))
        arguments += ["--buildspec-override", open(os.path.join(HERE, "buildspec-ci.yml")).read()]
    if compute:
        arguments += ["--compute-type-override", compute]
    started = cb.aws(*arguments)["build"]
    print("1/3 build started for %s (%s): %s" % (name, sha[:10], started["id"]))
    return started["id"]


def report(build_id):
    current = cb.build(build_id)
    if not current.get("buildComplete"):
        print("the build is still %s (%s)" % (current["buildStatus"], current.get("currentPhase")))
        return 2
    name = next((v["value"] for v in current["environment"].get("environmentVariables", [])
                 if v["name"] == "CI_REF"), "") or current.get("sourceVersion", "")
    key = "ci/%s/%s/result.json" % (cb.ref_key(name), build_id.split(":")[-1])
    minutes, cost = cb.minutes_and_cost(current)
    text = cb.s3_text(key)
    print("3/3 result for %s at %s" % (name, (current.get("resolvedSourceVersion") or "")[:10]))
    if not text:
        print("FAIL the build ended %s without writing s3://%s/%s" % (current["buildStatus"], cb.BUCKET, key))
        print(cb.log_tail(current))
        print("build minutes: %d, about USD %.2f" % (minutes, cost))
        return 1
    result = json.loads(text)
    harness, service = result["harness"], result["service_tests"]
    print("\n%s" % (harness["summary_line"] or "the harness did not finish"))
    print("harness: %d passed, %d failed, %d known-failing" % (
        harness["passed"], len(harness["failed"]), len(harness["known_failing"])))
    if harness["known_failing"]:
        print("Known-failing before this work (not regressions): %s" % ", ".join(harness["known_failing"]))
    if harness["known_failing_now_passing"]:
        print("Listed as failing in the baseline but passing here: %s" % ", ".join(harness["known_failing_now_passing"]))
    print("service tests: %s of %s pass, %s fail" % (
        service.get("pass", "?"), service.get("tests", "?"), service.get("fail", "?")))
    if result["slow_python"]:
        print("slow Python suites: %s" % ", ".join(
            "%s %s" % (name, "ok" if ok else "FAIL") for name, ok in sorted(result["slow_python"].items())))
    print("tests took %ds; build %d billed minutes on %s, about USD %.2f" % (
        result["test_seconds"], minutes, current["environment"]["computeType"], cost))
    print("results: s3://%s/%s" % (cb.BUCKET, key.rsplit("/", 1)[0] + "/"))
    if harness["new_failures"]:
        print("\nREGRESSIONS: %s" % ", ".join(harness["new_failures"]))
    if result["regression"]:
        if not harness["new_failures"]:
            print("\nFAIL: see the service tests, slow suites or an unfinished harness above")
        return 1
    print("\nNo regressions against tools/harness/baseline.json.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ref", nargs="?", help="branch, tag or commit; default the current HEAD")
    parser.add_argument("--wait-minutes", type=int, default=35)
    parser.add_argument("--compute", help="override the instance size, for example BUILD_GENERAL1_LARGE")
    parser.add_argument("--no-wait", action="store_true", help="start the build, print its id and return")
    parser.add_argument("--status", metavar="BUILD_ID", help="one status line for a build")
    parser.add_argument("--collect", metavar="BUILD_ID", help="print the result of a finished build")
    options = parser.parse_args()
    if options.status:
        current = cb.build(options.status)
        print(current["buildStatus"], current.get("currentPhase", ""))
        return 0
    if options.collect:
        return report(options.collect)
    build_id = start(options.ref, options.compute)
    if options.no_wait:
        return 0
    print("2/3 waiting (limit %d minutes)" % options.wait_minutes)
    cb.wait(build_id, options.wait_minutes)
    return report(build_id)


if __name__ == "__main__":
    sys.exit(main())
