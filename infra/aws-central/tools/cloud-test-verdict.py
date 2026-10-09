#!/usr/bin/env python3
"""Accept, or refuse, an AWS test run as the verdict for what deploy.sh is about to deploy.

    cloud-test-verdict.py <CodeBuild build id> <account id> <region>

tools/cloud/ci.sh leaves each run's result at
s3://pothole-reporter-ml-<account>-<region>/ci/<short commit>/<build uuid>/result.json.
The result stands in for a local `npm test` only when it is about exactly this tree:
the build tested HEAD, no tracked file is modified, the service suite ran with no
failure and the harness reported no regression. Exit 0 and one line on success; exit 1
with the reason otherwise.
"""
import json
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    build, account, region = sys.argv[1:4]
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
    if result.get("regression") is not False:
        problems.append("the harness reported a regression")
    if problems:
        sys.exit("cloud result refused: " + "; ".join(problems))
    print("service suite on AWS for %s: %s of %s pass, %s fail, %s skipped; harness %s" % (
        head[:7], service["pass"], service["tests"], service["fail"], service.get("skipped"),
        (result.get("harness") or {}).get("summary_line")))


if __name__ == "__main__":
    main()
