# Camera and GPS recovery verification — 2026-09-25

## Changes

- Zero GPS speed now samples once a second, not once every eight seconds.
- Camera liveness is checked before GPS/cadence gates. Ended/stalled streams are reopened automatically, with bounded retries and late-stream cleanup after Stop.
- Recording keeps its segment numbering and pending writes across camera replacement.
- Android WebView uses the native fused-location plugin. An independent bounded location probe recovers silent watches. Stale fixes remain rejected.
- Native drive capture times out after three seconds and rebinds the camera after two failed captures.
- Per-capture timeout timers are cleared after completion.

## Passed

- drive_camera_gps_recovery_test.py: zero-speed capture, lost camera without GPS, silent GPS watch, late camera recovery after Stop, late native watch registration, recording continuity.
- capture_cadence_test.py: moving, missing speed, coarse GPS jitter, zero speed.
- drive_no_gps_test.py, drive_location_denied_test.py, drive_start_stop_test.py, drive_stop_backlog_test.py, drive_native_location_denied_test.py.
- llm_contract_parity_test.py, full_frame_invariant_test.py, git diff --check.
- Android debug compilation and APK assembly.
- Android emulator: native GPS at 12.9716/77.5946; 71 frames checked before forced track termination; replacement stream live and 90 frames checked afterward, zero dropped at that observation. Stop completed.
- All four packaged HTML copies have identical SHA-256 hashes.

## Limitations

- Detection was mocked in capture/recovery tests and emulator checks; no model accuracy or real-road recall claim follows from these tests.
- Android unit suite: 253/253 passed on the release-preparation rerun. Updated three obsolete test assumptions: normalization now tests all configured languages, while the Kannada-specific prompt test explicitly selects Kannada. No production prompt was changed.
- Emulator GPS and camera do not establish performance on physical phones, under thermal load, or in poor satellite reception.
- Analysis still requires fresh location; no coordinates are fabricated when GPS is unavailable.
- QA APK uses application ID dev.aiengg.potholereporter.recoveryqa and debug signing to coexist with the installed app without deleting its data. It is not a Play Store release or an update APK for the existing app.
- Release preparation: 1.39.5 (74). Play Store rollout remains blocked by unavailable upload signing configuration and Play Console access. Website publication is verified separately against the deployed commit.

## Additional manual-photo GPS coverage

- Manual capture now uses the native fused-location provider on Android, including the permission-aware camera-open sampler.
- First-fix timeout increased from two to ten seconds; late, stale and hanging providers tested. Unknown accuracy is not converted to zero metres.
- Passed manual_native_gps_test.py, privacy_consent_test.py, manual_capture_cancel_test.py, manual_analysis_race_test.py and native_capture_safety_contract_test.py.
- Passed photo_retry_routing_test.py and photo_report_detail_test.py with mocked resolver responses. These do not establish live contractor coverage or reproduce the user's missing-name observation.
- Public AWS health endpoint responds successfully; it reports OpenAI configured but YOLO fallback not configured. No backend deployment was performed for this client-only patch.
