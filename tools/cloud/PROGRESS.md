# Progress (read this first on resume)

- 2026-10-07: worktree created from origin/main 0398f35. PLAN.md written.
- Facts verified: Device Farm trial 991.33 of 1000 minutes left; project has one old
  BUILTIN_FUZZ run; no CodeBuild projects exist; secret and bucket exist.
- Next: Device Farm (pool, test package, phone-test.sh, run on 1.40.0).
- Device Farm test written: tools/cloud/device-farm/ (tests/first_run.py, testspec.yml,
  devices.json, run.py) and tools/cloud/phone-test.sh. Pool: Galaxy A15 (14), Redmi
  Note 13 (15), Pixel 8a (17), Galaxy A13 5G (11). Not yet run.
- Trial run 1 (Galaxy A15 only) scheduled 18:02 IST:
  arn:aws:devicefarm:us-west-2:695656921622:run:5425ff41-703f-49dd-b67a-dae7a8b54b98/1c165377-e505-4f3c-b97e-4e0b36d3e050
  Poll: tools/cloud/phone-test.sh --status <arn>; then --collect <arn>.
  (One rejected upload first-run-33f90b2a9f0e.zip exists: the test file name must start with test.)
