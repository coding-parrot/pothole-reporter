#!/usr/bin/env bash
# Local checks that guard shipped behaviour. Some mocked checks still read the test key
# from .env, but no live service is contacted unless RUN_LIVE_TESTS=1 is explicit.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python3
fail=0

# Keep the central API contract and the Android/native bridge in the same release
# gate as the browser workflow. Project-qualified Gradle targets avoid trying to
# assemble the third-party Capacitor plugins' own test APKs.
printf "%-24s " "central_server"
if out=$(cd server && npm test 2>&1); then
  echo "${out##*$'\n'}"
else
  echo "FAIL"; echo "$out" | tail -12 | sed 's/^/    /'; fail=1
fi

printf "%-24s " "android_app"
if out=$(cd android-app/android && ./gradlew \
    :app:testDebugUnitTest :app:assembleDebugAndroidTest 2>&1); then
  echo "${out##*$'\n'}"
else
  echo "FAIL"; echo "$out" | tail -12 | sed 's/^/    /'; fail=1
fi

start_server() {
  (nohup python3 tests/serve_app.py --port 8765 >/tmp/pothole-srv.log 2>&1 &)
  for _ in $(seq 1 20); do
    curl -s -o /dev/null http://localhost:8765/index.html && return 0
    sleep 0.5
  done
  return 1
}
# The suite launches a browser per test and the little server has died mid-run before,
# which reads as a test failure and is not one. Check it before each test, restart if gone.
ensure_server() {
  curl -s -o /dev/null --max-time 3 http://localhost:8765/index.html && return 0
  echo "    (restarting the static server)"
  pkill -f "tests/serve_app.py --port 8765" >/dev/null 2>&1
  start_server
}

pkill -f "http.server 8765" >/dev/null 2>&1
pkill -f "tests/serve_app.py --port 8765" >/dev/null 2>&1
start_server || { echo "could not start the static server"; exit 1; }
trap 'pkill -f "tests/serve_app.py --port 8765" >/dev/null 2>&1' EXIT

TESTS="llm_contract_parity_test standalone_default_test unit_test server_client_contract_test timeout_contract_test email_only_flow_test native_email_cache_test browser_civic_cache_migration_test central_resolution_isolation_test eval_contract_test drive_evidence_dedupe_test manual_analysis_race_test video_import_budget_test footage_metadata_test footage_backpressure_test drive_start_stop_test orphan_footage_test capture_cadence_test letter_test tender_determinism_test tender_source_registry_test national_highway_contracts_test storage_commit_test stalled_body_test
       stored_xss_test public_map_test privacy_consent_test ui_text_test routing_test nh_test gis_failure_test footage_test routing_failure_wording_test gandhinagar_shared_scope_test"

for t in $TESTS; do
  ensure_server || { echo "$t SKIPPED, no server"; fail=1; continue; }
  printf "%-24s " "$t"
  if out=$($PY "tests/$t.py" 2>&1); then
    echo "${out##*$'\n'}"
  else
    # The tests that query Karnataka's GIS live share one flaky government service, and it
    # rate-limits when the whole suite runs back to back. A single retry tells a real
    # regression apart from the state's server having a moment.
    case "$t" in
      routing_test|nh_test|gis_failure_test)
        sleep 5
        ensure_server
        if out=$($PY "tests/$t.py" 2>&1); then
          echo "${out##*$'\n'} (passed on retry)"
          continue
        fi
        ;;
    esac
    echo "FAIL"; echo "$out" | tail -12 | sed 's/^/    /'; fail=1
  fi
done
echo
[ "$fail" = "0" ] && echo "ALL TESTS PASS" || { echo "SOME TESTS FAILED"; exit 1; }
