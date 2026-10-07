# Cloud test pipeline: plan, as executed (7 Oct 2026)

Goal: heavy testing runs on AWS, not on the Mac. The Mac keeps one job: signing the
release (the upload keystore and its password never leave it) and the Play upload.

## What runs where

| Work | Where | Command | Measured cost per run |
|---|---|---|---|
| First run on four real phones | AWS Device Farm, us-west-2, project `pothole-reporter`, pool `pothole-india-phones` | `tools/cloud/phone-test.sh <signed apk>` | about 11 device minutes (ceiling 40: 10 a phone). Free inside the trial, then USD 0.17 a minute. About 30 shared detections a phone, about USD 0.05 of OpenAI |
| Service tests, slow Python suites, full harness (247 suites) | AWS CodeBuild, ap-south-1, project `pothole-reporter-ci`, 2 vCPU, on demand | `tools/cloud/ci.sh [ref]` | 10 billed minutes, USD 0.05 |
| Production prompt against a candidate on N labelled frames | same project, eval role, `buildspec-eval.yml` | `tools/cloud/eval-prompt.sh --candidate <file> --frames N --budget-usd X` | 2 billed minutes (USD 0.01) plus about USD 0.0007 a frame of OpenAI, stopped at X |
| Classifier scorecard | the training instance | unchanged | unchanged |
| Build and sign the release, Play upload | the Mac | `tools/build-play-release.sh` | none |

## How each part works

- Phones: Galaxy A15 (Android 14), Redmi Note 13 (15), Pixel 8a (17), Galaxy A13 5G
  (11, 720 px wide); why each is in `device-farm/devices.json`. Appium (UiAutomator2,
  native context) on Device Farm's Appium 3 Python host; the client is plain standard
  library Python, so the host installs nothing. The release build has no WebView
  context, so controls are found by their text in the accessibility tree and permission
  sheets with `tools/harness/permission-button.py`. Device Farm sets the location to
  Bengaluru. The last drive on each phone runs with Appium closed (two adb taps on a
  fresh process), so a failure cannot be blamed on the test reading the screen.
- Harness: the buildspec rebuilds `.venv` from `requirements-ci.txt` (Python 3.9, as on
  the Mac), installs Chromium, runs `npm ci` and `cap copy`, and writes `result.json`
  and logs to `s3://pothole-reporter-ml-695656921622-ap-south-1/ci/<ref>/<build id>/`.
  `ci.sh` judges it against `tools/harness/baseline.json`. A ref from before the
  buildspec existed is built with this checkout's copy.
- Private inputs. `.venv`: rebuilt. `.env`: not used; `nh_test.py`,
  `stream_completion_test.py` and `stalled_body_test.py` read `OPENAI_API_KEY` but
  answer every model call themselves, so CI sets a placeholder (`storage_commit_test.py`
  loads the file and uses nothing from it). `eval/images`: `gis_failure_test.py` and
  `routing_test.py` serve one seed photo, now at `ci/fixtures/eval/images/seed/`; no
  other labelled image is read by any suite.
- Eval: half the frames labelled damaged, half undamaged, test split first, fixed by a
  seed. Both arms send the production drive request; only the base prompt differs. The
  key comes from Secrets Manager inside the build. HTTP/1.1 keep-alive. A call starts
  only while the budget covers it. Results under `evals/<run id>/`.

## Roles

`pothole-reporter-ci-codebuild`: logs and the `ci/` prefix, nothing else.
`pothole-reporter-eval-codebuild`: logs, the detector secret, `v1-work/manifest.jsonl`
and `v1-work/frames/` read-only, the `evals/` prefix. A second role, so that a harness
build of any branch can never read the OpenAI key.

## Not covered on a real phone

The report card's ward tender list and the Email composer. The app's Photo button opens
the system camera only, so a pushed file cannot enter; both controls appear only after
the production detector confirms damage and the production service confirms the report,
which would put a test pothole on the public map. A debuggable build of the same commit
would cover both through the WebView context the test already uses when it finds one.
