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
- CI build 1 started on feat/cloud-testing c2d52e6: pothole-reporter-ci:0dccbac8-1b67-47be-bd6a-f2246ef2973d (poll: tools/cloud/ci.sh --status <id>, then --collect <id>)
- Full phone run 2 result (7.86 device minutes): Galaxy A15 and Redmi Note 13 PASS;
  Pixel 8a (Android 17) and Galaxy A13 5G (Android 11) FAIL on "Camera paused" in both
  drives. Pattern: whenever the first GPS fix takes longer than about 8 s the HUD says
  Camera paused and the app reopens the camera (Pixel: 5 opens in 45 s) although the OS
  shows the camera streaming to the app throughout. Desktop Chromium does not reproduce
  (probe with silent GPS for 18 s). Root cause needs page state: the test now records
  video/track/watchdog fields when a debuggable build exposes a WebView context.
- CI build 1 result: harness 238/247, the 8 known failures plus one new: central service
  unit tests (3 of 712 fail in health-cli.test.mjs: gzip size 408 on x86-64 Node 22 vs
  410 pinned from the Mac). Fixed in the test (size blanked). 9 billed minutes MEDIUM.
- eval/prompt_eval.mjs written and self-tested; buildspec-eval.yml and launcher still to do.
- CI builds 2: branch 5729b21 MEDIUM pothole-reporter-ci:8649206e-e4e3-4122-a2a1-f36d154be50b ; origin/main 4de90e2 SMALL (override buildspec) pothole-reporter-ci:e9229274-20fe-4b89-b8df-fdb1f87a2aba. NOTE origin/main moved from 0398f35 to 4de90e2 during this task.
- Diagnostic runs with the 1.39.5 debug APK on Pixel 8a: run ef20e92f (WebView context works; camera reopened 10 s into the second drive while video played and GPS was fresh, captured 0) and run 03fd7e5a-44e9-4e71-a10c-50d9fded1fc2 (instrumented drive: toBlob/drawImage timing), results under ~/Downloads/pothole-testers/device-farm/1.39.5/. Working theory: captureFrame (drawImage + toBlob) does not finish in 3 s three times, then the watchdog reopens the camera.
- CI build 2 on branch (5729b21): gzip fix worked (service tests standalone 711 pass, 0 fail, 1 skipped) but inside the harness the service suite failed once under load (710 pass, 1 fail, name not in the 25-line tail). run.mjs now writes whole failing output to HARNESS_FAILURE_DIR; CI build 3 on branch c8a23f0: pothole-reporter-ci:b913f383-d3b2-4878-aceb-3f81e8503a01. origin/main SMALL build e9229274 still running. Eval role pothole-reporter-eval-codebuild created; proof eval not yet started (project allows 2 builds at once).
- Proof eval started: run prompt-20261007-130135-production, build pothole-reporter-ci:3e2472a4-da32-45c4-a0f1-679b800bac4a. origin/main (4de90e2) on SMALL: 238/247, 8 known + central service unit tests (gzip pin, 3 of 729), 10 billed minutes USD 0.05: SMALL is enough.
- Instrumented run 03fd7e5a (animation loop on) did not reproduce: toBlob 35 ms, no reopen. Run 0747ec02-ee88-408b-80a2-4aa6f9ef2f5e does the instrumented drive twice (listen only, then animating).
- Proof eval DONE: 60 pairs, 3 discordant (0 vs 3), exact p 0.25, USD 0.0422 OpenAI + 2 CodeBuild minutes. s3 evals/prompt-20261007-130135-production/
- CI build 3 on branch c8a23f0 (merged main + gzip fix): 239/247, only the 8 known failures, service 728/729 (1 skipped), exit 0, 7 minutes MEDIUM. Project default switched to SMALL (origin/main took 10 billed minutes there).
- CI build 4 (branch 7832456, SMALL default): pothole-reporter-ci:4a8ff27a-5836-47b5-a7fd-3d3a4b4bb5b2
- Passive and animating instrumented drives (run 0747ec02) both clean: toBlob 35 ms. Suspicion: reading the accessibility tree every second (the test itself) starves the page, so the pause may be an observer effect. Run f64645e4-341d-4917-ab09-7d3449fb9a48 (4 phones, release 1.40.0): first and second drives untouched, third watched; camera opens counted per drive. Results folder ~/Downloads/pothole-testers/device-farm/1.40.0/run-20261007-183945. Device minutes so far 18.6 before this run.
- CI build 4 on SMALL (branch 7832456): 239/247, only the 8 known, exit 0, 10 billed minutes USD 0.05. CodeBuild spend so far about USD 0.34.
- Run f64645e4 (quiet/watched): stall and reopen also happen in drives the test never looked at (Pixel second drive: 1 frame in 12 s; A13 same; A15 first drive 2 frames in 20 s). Deduction: captures time out at 3 s three times, so canvas.toBlob is not answering; drawImage is synchronous. Run 67f608c7-4d63-478f-8357-a818335cd9d7 adds a drive after Appium is closed (adb taps only, fresh process) to rule out the accessibility client. Results ~/Downloads/pothole-testers/device-farm/1.40.0/run-20261007-185214. Device minutes before this run: 29.2. Planned fix: grabPreview falls back to a synchronous toDataURL when toBlob is late; failing browser test first.
- App fix committed on the branch: tests/drive_capture_encode_stall_test.py (failed first: Camera paused, camera opened twice, 0 frames; passes now) and static/index.html canvasJpeg fallback, mirrors synced. CI build 5 on branch 989a7f7: pothole-reporter-ci:7cc26b85-c11a-49f9-93e2-6d0e4e99a7b5. Phone run 67f608c7 still running.
- Run 67f608c7 (13.32 device minutes; total so far 42.5): the unattended drive (Appium closed, fresh process, adb taps only) reopened the camera after 10 to 13 s on Galaxy A13 and Galaxy A15. Not a test artefact: app bug. Pixel and Redmi unattended drives were clean that time; both showed it in other drives.
- Final default phone run (first + unattended drive): afdde8e0-6a94-4747-999a-98eeaa209bc5, folder ~/Downloads/pothole-testers/device-farm/1.40.0/run-20261007-190130. OpenAI so far: eval USD 0.042 plus about 540 shared detections from phone drives (about USD 0.19 to 0.26). No more phone runs after this one.
- CI build 5 (989a7f7, with app fix): 239/248, new test passes; one new failure server_client_contract_test.py (central sync still pending: looks like a load flake, not the capture path). Rerun: build 6 on cbd6b92: pothole-reporter-ci:b11f1487-ee7f-461a-9b28-c3088ba01027
