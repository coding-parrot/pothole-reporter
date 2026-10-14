#!/usr/bin/env python3
"""Accept, or refuse, an AWS test run as the verdict for what deploy.sh is about to deploy.

    cloud-test-verdict.py <CodeBuild build id> <account id> <region> [<baseline commit>/<build uuid>]

tools/cloud/ci.sh leaves each run's result at
s3://pothole-reporter-ml-<account>-<region>/ci/<short commit>/<build uuid>/result.json.
The result stands in for a local `npm test` only when it is about exactly this tree:
the build tested HEAD, no tracked file is modified, the service suite ran with no
failure and the harness reported no regression. Exit 0 and one line on success; exit 1
with the reason otherwise.

A baseline (CLOUD_BASELINE in deploy.sh) is an AWS run of an earlier commit on HEAD's
history, such as main. A harness test that also failed there is not this change's doing:
on 14 Oct 2026 road_notice_matching_test.py failed on main itself because four State
packs passed their review date, and the deploy gate stopped every server change with
it. Only tests listed as new failures in BOTH runs are excused, and they are printed.
"""
import json
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def fetch(account, region, key):
    fetched = subprocess.run(["aws", "s3", "cp", "s3://pothole-reporter-ml-%s-%s/%s" % (account, region, key), "-",
                              "--region", region], capture_output=True, text=True)
    return json.loads(fetched.stdout) if fetched.returncode == 0 else None


def new_failures(result):
    return set((result.get("harness") or {}).get("new_failures") or [])


def main():
    build, account, region = sys.argv[1:4]
    baseline = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("cloud result refused: tracked files are modified, so the run did not test this tree")
    key = "ci/%s/%s/result.json" % (git("rev-parse", "--short", "HEAD"), build.split(":", 1)[-1])
    fetched = subprocess.run(["aws", "s3", "cp", "s3://pothole-reporter-ml-%s-%s/%s" % (account, region, key), "-",
                              "--region", region], capture_output=True, text=True)
    if fetched.returncode != 0:
        sys.exit("cloud result refused: no result for %s under build %s" % (head[:7], build))
    result = json.loads(fetched.stdout)
    service = result.get("service_tests") or {}
    problems = []
    if result.get("commit") != head:
        problems.append("the build tested %s, not HEAD" % result.get("commit"))
    if not (service.get("pass", 0) > 0 and service.get("fail", 1) == 0
            and service.get("cancelled", 1) == 0 and service.get("exit", 1) == 0):
        problems.append("the service suite did not pass: %s" % service)
    excused = set()
    if result.get("regression") is not False:
        failing = new_failures(result)
        base = None
        if baseline:
            base_commit = baseline.split("/", 1)[0]
            base = fetch(account, region, "ci/%s/result.json" % baseline)
            if base is None:
                problems.append("no baseline result at ci/%s" % baseline)
            elif subprocess.run(["git", "merge-base", "--is-ancestor", base.get("commit", base_commit), head]).returncode != 0:
                problems.append("the baseline %s is not on HEAD's history" % base_commit)
                base = None
        if base is not None and failing and failing <= new_failures(base):
            excused = failing
        else:
            problems.append("the harness reported a regression%s" % (
                ": %s" % ", ".join(sorted(failing - (new_failures(base) if base else set()))) if failing else ""))
    if problems:
        sys.exit("cloud result refused: " + "; ".join(problems))
    print("service suite on AWS for %s: %s of %s pass, %s fail, %s skipped; harness %s" % (
        head[:7], service["pass"], service["tests"], service["fail"], service.get("skipped"),
        (result.get("harness") or {}).get("summary_line")))
    if excused:
        print("failing on the baseline too, so not this change: %s" % ", ".join(sorted(excused)))


if __name__ == "__main__":
    main()
