# Cloud test pipeline: plan

Goal: heavy testing runs on AWS, not on the Mac. The Mac keeps one job: signing the
release (the upload keystore and its password never leave it).

## What runs where

| Work | Where | Command | Cost per run |
|---|---|---|---|
| First run on real phones (install, Home, Drive, notice, both permission sheets, 20 s live drive, Stop, Settings, crash/ANR/JS error scan, screenshots) | AWS Device Farm, us-west-2, project `pothole-reporter`, pool of 4 phones | `tools/cloud/phone-test.sh <signed apk>` | at most 40 device minutes (10 per phone, job timeout). Free while trial minutes last, then USD 0.17 a minute, so at most USD 6.80 |
| Service tests (712), Python suites, full harness (246 suites) | AWS CodeBuild, ap-south-1, project `pothole-reporter-ci`, on demand only | `tools/cloud/ci.sh [ref]` | one build under 15 minutes on the smallest instance that manages it; a few US cents |
| Prompt A against prompt B on N labelled frames | CodeBuild, same project, a second buildspec | `tools/cloud/eval-prompt.sh --candidate <file> --frames N --budget-usd X` | CodeBuild cents plus OpenAI spend, hard-stopped at X from the usage fields |
| Classifier scorecard | already on the training instance | unchanged | unchanged |
| Build and sign the release, Play upload | the Mac | `tools/build-play-release.sh` | none |

## Decisions

- Phones are driven through Appium (UiAutomator2) in the native context. The release
  build is not debuggable, so there is no WebView context: controls are found in the
  accessibility tree by their text, system permission sheets by the same rules as
  `tools/harness/permission-button.py`. The test client is plain Python standard
  library speaking WebDriver to the Appium server on the test host: nothing to install.
- Device Farm sets the phone's location to Bengaluru so Drive gets a fix.
- Private inputs. `.venv`: rebuilt in CodeBuild from `tools/cloud/requirements-ci.txt`.
  `.env`: three suites read `OPENAI_API_KEY` but stub every model call, so CI sets a
  placeholder and no real key enters the harness build. `eval/images`: two suites serve
  one seed photo; it goes to `ci/fixtures/` in the private bucket.
- The eval reads the OpenAI key from Secrets Manager (`pothole-reporter-central/detector`)
  at run time, inside the build, and never prints it.
- Results: `s3://pothole-reporter-ml-695656921622-ap-south-1/ci/<ref>/<build id>/` and
  `.../evals/<run id>/`; phone results in `~/Downloads/pothole-testers/device-farm/<version>/`.

## Limits for this task

Device Farm 80 device minutes, CodeBuild USD 2, OpenAI USD 0.50. Every wait is bounded.
No webhooks, no schedules, no always-on resources. Everything taggable carries
`project=pothole-reporter-ci`.

## Order

1. Device Farm test package, pool, `phone-test.sh`, run on 1.40.0 (79).
2. CodeBuild project, role, buildspec, `ci.sh`, run on `origin/main`.
3. `eval-prompt.sh`, proof run (60 frames, USD 0.10, production prompt against itself).
