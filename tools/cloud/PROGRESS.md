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
- Trial run 1 result (Galaxy A15, 1.97 device minutes, results in
  ~/Downloads/pothole-testers/device-farm/1.40.0/run-20261007-180154): the whole flow
  works through the accessibility tree. FINDING: HUD said "Camera paused (another app may
  be using it)" about 7 s after the location sheet was granted; logcat shows the app
  closed and reopened the camera 13 s after first opening it. 18 frames went to the
  shared production detector in 20 s (about USD 0.009 a phone).
- Test now samples the HUD every 1.5 s, counts camera opens from logcat, and runs a
  second 10 s drive as a control (no sheets). buildspec-ci.yml and requirements-ci.txt
  drafted, not yet used.
- Full run 2 (4 phones, pool pothole-india-phones created) scheduled:
  arn:aws:devicefarm:us-west-2:695656921622:run:5425ff41-703f-49dd-b67a-dae7a8b54b98/f2dff49f-08af-45bd-8c48-e4302b102a5b
  results folder ~/Downloads/pothole-testers/device-farm/1.40.0/run-20261007-180839
- CodeBuild: role pothole-reporter-ci-codebuild (logs + ci/ prefix only) and project
  pothole-reporter-ci (MEDIUM, standard:7.0, no webhook) created. Seed photo uploaded to
  ci/fixtures/eval/images/seed/IMG20260720144404.jpg (needed by gis_failure_test.py and
  routing_test.py only). ci.sh/ci.py/cb.py written. Next: push branch, run ci.sh.
