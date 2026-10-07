#!/usr/bin/env python3
"""Upload a signed APK to AWS Device Farm, run the first-run test on the phone pool,
and bring the results back. Called by tools/cloud/phone-test.sh; see its header.

Standard library plus the aws CLI. Every wait has a limit.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "../../.."))
REGION = "us-west-2"  # the only region Device Farm's phones live in
PROJECT_NAME = os.environ.get("DEVICEFARM_PROJECT", "pothole-reporter")
PACKAGE = "dev.aiengg.potholereporter"
TAGS = [{"Key": "project", "Value": "pothole-reporter-ci"}]
RESULTS_ROOT = os.path.expanduser(
    os.environ.get("POTHOLE_DEVICE_FARM_DIR", "~/Downloads/pothole-testers/device-farm"))
PRICE_PER_MINUTE = 0.17
CONFIG = json.load(open(os.path.join(HERE, "devices.json")))
# Files that must never be uploaded anywhere, whatever the caller passes.
FORBIDDEN = re.compile(r"\.(jks|keystore|p12|pem|env)$|keystore\.properties$|/\.android/|/\.env$", re.I)


def aws(*args, timeout=60):
    command = ["aws", "devicefarm", *args, "--region", REGION, "--output", "json"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        sys.exit("aws devicefarm %s failed: %s" % (args[0], done.stderr.strip()[:600]))
    return json.loads(done.stdout) if done.stdout.strip() else {}


def sha12(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


def project_arn():
    for project in aws("list-projects")["projects"]:
        if project["name"] == PROJECT_NAME:
            return project["arn"]
    sys.exit("Device Farm project %s does not exist" % PROJECT_NAME)


def upload(project, path, name, kind, limit_seconds=300):
    """Upload path under name, or reuse a finished upload with that exact name."""
    if FORBIDDEN.search(path):
        sys.exit("refusing to upload %s: it looks like a key or a secret" % path)
    for existing in aws("list-uploads", "--arn", project, "--type", kind)["uploads"]:
        if existing["name"] == name and existing["status"] == "SUCCEEDED" \
                and existing.get("category") == "PRIVATE":
            print("  reusing upload %s" % name)
            return existing
    created = aws("create-upload", "--project-arn", project, "--name", name, "--type", kind)["upload"]
    put = subprocess.run(["curl", "-sS", "--fail", "--max-time", "600", "-T", path, created["url"]],
                         capture_output=True, text=True)
    if put.returncode != 0:
        sys.exit("upload of %s failed: %s" % (name, put.stderr.strip()[:300]))
    deadline = time.time() + limit_seconds
    while time.time() < deadline:
        current = aws("get-upload", "--arn", created["arn"])["upload"]
        if current["status"] == "SUCCEEDED":
            print("  uploaded %s" % name)
            return current
        if current["status"] == "FAILED":
            sys.exit("Device Farm rejected %s: %s" % (name, current.get("message", "")[:400]))
        time.sleep(4)
    sys.exit("upload %s did not validate within %ss" % (name, limit_seconds))


def build_test_package(folder):
    """tests/first_run.py, the permission rules the emulator smoke uses, requirements.txt."""
    target = os.path.join(folder, "first-run.zip")
    members = [
        (os.path.join(HERE, "tests", "first_run.py"), "tests/first_run.py"),
        (os.path.join(REPO, "tools", "harness", "permission-button.py"), "tests/permission-button.py"),
        (os.path.join(HERE, "requirements.txt"), "requirements.txt"),
    ]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # stable hash
            info.external_attr = 0o644 << 16
            archive.writestr(info, open(source, "rb").read(), zipfile.ZIP_DEFLATED)
    return target


def device_pool(project):
    arns = [device["arn"] for device in CONFIG["devices"]]
    rules = [{"attribute": "ARN", "operator": "IN", "value": json.dumps(arns)}]
    for pool in aws("list-device-pools", "--arn", project, "--type", "PRIVATE")["devicePools"]:
        if pool["name"] == CONFIG["pool_name"]:
            if pool["rules"] != rules:
                aws("update-device-pool", "--arn", pool["arn"], "--rules", json.dumps(rules))
                print("  device pool %s updated" % CONFIG["pool_name"])
            return pool["arn"]
    pool = aws("create-device-pool", "--project-arn", project, "--name", CONFIG["pool_name"],
               "--description", "Phones that stand for Indian users; see tools/cloud/device-farm/devices.json",
               "--rules", json.dumps(rules))["devicePool"]
    aws("tag-resource", "--resource-arn", pool["arn"], "--tags", json.dumps(TAGS))
    print("  device pool %s created" % CONFIG["pool_name"])
    return pool["arn"]


def schedule(apk, only, name):
    if not apk.endswith(".apk") or not os.path.isfile(apk):
        sys.exit("give the path to a signed .apk (got %s)" % apk)
    project = project_arn()
    print("1/4 uploading the APK and the test package")
    app = upload(project, apk, "%s-%s.apk" % (os.path.basename(apk)[:-4], sha12(apk)), "ANDROID_APP")
    metadata = json.loads(app.get("metadata") or "{}")
    if metadata.get("package_name") != PACKAGE:
        sys.exit("that APK is %s, not %s" % (metadata.get("package_name"), PACKAGE))
    version = metadata.get("version_name") or "unknown"
    build = metadata.get("version_code") or "0"
    with tempfile.TemporaryDirectory() as folder:
        package = build_test_package(folder)
        tests = upload(project, package, "first-run-%s.zip" % sha12(package), "APPIUM_PYTHON_TEST_PACKAGE")
    spec_path = os.path.join(HERE, "testspec.yml")
    spec = upload(project, spec_path, "first-run-%s.yml" % sha12(spec_path), "APPIUM_PYTHON_TEST_SPEC")

    print("2/4 scheduling the run")
    arguments = ["schedule-run", "--project-arn", project, "--app-arn", app["arn"],
                 "--name", name or "%s (%s) first run" % (version, build),
                 "--test", json.dumps({"type": "APPIUM_PYTHON", "testPackageArn": tests["arn"],
                                       "testSpecArn": spec["arn"]}),
                 "--configuration", json.dumps({
                     "location": CONFIG["location"], "billingMethod": "METERED", "locale": "en_US",
                     "radios": {"wifi": True, "bluetooth": False, "nfc": False, "gps": True}}),
                 "--execution-configuration", json.dumps({
                     "jobTimeoutMinutes": CONFIG["job_timeout_minutes"], "videoCapture": True})]
    if only:
        chosen = [d for d in CONFIG["devices"] if only.lower() in d["name"].lower()]
        if not chosen:
            sys.exit("no device in devices.json matches %r" % only)
        arguments += ["--device-selection-configuration", json.dumps({
            "filters": [{"attribute": "ARN", "operator": "IN", "values": [d["arn"] for d in chosen]}],
            "maxDevices": len(chosen)})]
        count = len(chosen)
    else:
        arguments += ["--device-pool-arn", device_pool(project)]
        count = len(CONFIG["devices"])
    run = aws(*arguments)["run"]
    aws("tag-resource", "--resource-arn", run["arn"], "--tags", json.dumps(TAGS))
    folder = os.path.join(RESULTS_ROOT, version, time.strftime("run-%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    json.dump({"run_arn": run["arn"], "apk": os.path.abspath(apk), "version": version,
               "build": build, "devices": count}, open(os.path.join(folder, "run.json"), "w"), indent=2)
    print("   run: %s" % run["arn"])
    print("   results folder: %s" % folder)
    print("   ceiling: %d phones x %d minutes = %d device minutes" % (
        count, CONFIG["job_timeout_minutes"], count * CONFIG["job_timeout_minutes"]))
    return run["arn"], folder


def wait(run_arn, limit_minutes):
    print("3/4 waiting for the run (limit %d minutes)" % limit_minutes)
    deadline = time.time() + limit_minutes * 60
    last = None
    while True:
        run = aws("get-run", "--arn", run_arn)["run"]
        line = "%s %s %s" % (run["status"], run.get("result", ""), json.dumps(run.get("counters", {})))
        if line != last:
            print("   %s %s" % (time.strftime("%H:%M:%S"), line), flush=True)
            last = line
        if run["status"] == "COMPLETED":
            return run
        if time.time() > deadline:
            aws("stop-run", "--arn", run_arn)
            sys.exit("the run did not finish in %d minutes; it has been stopped. "
                     "Collect what exists with --collect %s" % (limit_minutes, run_arn))
        time.sleep(20)


def find_folder(run_arn):
    if os.path.isdir(RESULTS_ROOT):
        for root, _, files in os.walk(RESULTS_ROOT):
            if "run.json" in files:
                try:
                    if json.load(open(os.path.join(root, "run.json")))["run_arn"] == run_arn:
                        return root
                except (ValueError, KeyError):
                    pass
    folder = os.path.join(RESULTS_ROOT, "unknown", run_arn.rsplit("/", 1)[-1])
    os.makedirs(folder, exist_ok=True)
    return folder


def download(url, path):
    done = subprocess.run(["curl", "-sS", "--fail", "--max-time", "300", "-o", path, url],
                          capture_output=True, text=True)
    return done.returncode == 0


def collect(run_arn, folder=None):
    print("4/4 collecting results")
    folder = folder or find_folder(run_arn)
    run = aws("get-run", "--arn", run_arn)["run"]
    rows, total_minutes = [], 0.0
    for job in aws("list-jobs", "--arn", run_arn)["jobs"]:
        device = job["device"]
        label = re.sub(r"[^A-Za-z0-9]+", "-", "%s-android-%s" % (device["name"], device["os"])).strip("-")
        target = os.path.join(folder, label)
        os.makedirs(target, exist_ok=True)
        minutes = float(job.get("deviceMinutes", {}).get("total", 0) or 0)
        total_minutes += minutes
        artifacts = aws("list-artifacts", "--arn", job["arn"], "--type", "FILE")["artifacts"]
        failed = job.get("result") != "PASSED"
        for artifact in artifacts:
            kind = artifact["type"]
            if kind == "CUSTOMER_ARTIFACT":
                archive = os.path.join(target, "customer-artifacts.zip")
                if download(artifact["url"], archive):
                    try:
                        with zipfile.ZipFile(archive) as bundle:
                            bundle.extractall(target)
                        os.remove(archive)
                    except zipfile.BadZipFile:
                        pass
            elif kind == "TESTSPEC_OUTPUT":
                download(artifact["url"], os.path.join(target, "testspec-output.txt"))
            elif failed and kind == "DEVICE_LOG":
                download(artifact["url"], os.path.join(target, "device-logcat.txt"))
            elif failed and kind == "VIDEO":
                download(artifact["url"], os.path.join(target, "video.mp4"))
        # The test's own files sit somewhere under the extracted log directory.
        verdict = None
        for root, _, files in os.walk(target):
            if "result.json" in files and os.path.basename(root) == "pothole":
                verdict = json.load(open(os.path.join(root, "result.json")))
                flat = os.path.join(target, "pothole")
                if os.path.abspath(root) != os.path.abspath(flat):
                    shutil.rmtree(flat, ignore_errors=True)
                    shutil.move(root, flat)
                break
        problems = []
        if verdict:
            problems += ["%s: %s" % (s["name"], s["detail"][:140]) for s in verdict["steps"] if not s["ok"]]
            problems += ["not run: %s" % name for name in verdict.get("not_run", [])]
            problems += ["logcat: %s" % line[:140] for line in verdict.get("logcat_findings", [])[:3]]
        elif failed:
            problems.append("no result.json came back (%s)" % (job.get("message") or "see testspec-output.txt"))
        passed = job.get("result") == "PASSED" and bool(verdict) and verdict.get("passed") is True
        rows.append({"device": device["name"], "os": device["os"], "job_result": job.get("result"),
                     "passed": passed, "minutes": minutes, "problems": problems,
                     "folder": target, "verdict": verdict})

    settings = aws("get-account-settings")["accountSettings"]
    trial = settings.get("trialMinutes", {})
    lines = ["Device Farm run %s: %s" % (run["name"], run.get("result")), ""]
    lines.append("%-26s %-8s %-7s %-8s %s" % ("device", "android", "result", "minutes", "what failed"))
    for row in rows:
        lines.append("%-26s %-8s %-7s %-8.2f %s" % (
            row["device"][:26], row["os"], "PASS" if row["passed"] else "FAIL", row["minutes"],
            row["problems"][0] if row["problems"] else ""))
        for extra in row["problems"][1:]:
            lines.append("%52s %s" % ("", extra))
    remaining = float(trial.get("remaining", 0) or 0)
    lines += ["", "device minutes used by this run: %.2f" % total_minutes,
              "free trial minutes left: %.2f of %.0f" % (remaining, float(trial.get("total", 0) or 0)),
              "cost: %s" % ("USD 0 (inside the free trial)" if remaining > 0
                            else "USD %.2f at USD %.2f a device minute" % (
                                total_minutes * PRICE_PER_MINUTE, PRICE_PER_MINUTE)),
              "results: %s" % folder]
    text = "\n".join(lines)
    print("\n" + text)
    open(os.path.join(folder, "summary.txt"), "w").write(text + "\n")
    json.dump([{k: v for k, v in row.items() if k != "verdict"} for row in rows],
              open(os.path.join(folder, "summary.json"), "w"), indent=2)
    return bool(rows) and all(row["passed"] for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("apk", nargs="?", help="path to the signed release APK")
    parser.add_argument("--only", help="run on the devices whose name contains this (a cheaper trial run)")
    parser.add_argument("--name", help="run name shown in the Device Farm console")
    parser.add_argument("--wait-minutes", type=int, default=45, help="give up and stop the run after this long")
    parser.add_argument("--no-wait", action="store_true", help="schedule, print the run ARN and return")
    parser.add_argument("--status", metavar="RUN_ARN", help="print one status line for a run")
    parser.add_argument("--collect", metavar="RUN_ARN", help="download and print the results of a finished run")
    options = parser.parse_args()
    os.environ.setdefault("AWS_PROFILE", "pothole")
    if options.status:
        run = aws("get-run", "--arn", options.status)["run"]
        print(run["status"], run.get("result", ""), json.dumps(run.get("counters", {})),
              "minutes", run.get("deviceMinutes", {}).get("total", 0))
        return 0
    if options.collect:
        return 0 if collect(options.collect) else 1
    if not options.apk:
        parser.error("give the path to the signed APK")
    run_arn, folder = schedule(options.apk, options.only, options.name)
    if options.no_wait:
        return 0
    wait(run_arn, options.wait_minutes)
    return 0 if collect(run_arn, folder) else 1


if __name__ == "__main__":
    sys.exit(main())
