#!/usr/bin/env bash
# Every check that guards a shipped behaviour. Needs .env with OPENAI_API_KEY.
# The live ones hit KGIS and OpenAI on purpose: the answers that matter are today's.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python3

start_server() {
  (cd android-app/www && nohup python3 -m http.server 8765 >/tmp/pothole-srv.log 2>&1 &)
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
  pkill -f "http.server 8765" >/dev/null 2>&1
  start_server
}

pkill -f "http.server 8765" >/dev/null 2>&1
start_server || { echo "could not start the static server"; exit 1; }
trap 'pkill -f "http.server 8765" >/dev/null 2>&1' EXIT

TESTS="unit_test eval_contract_test persistent_dedupe_test footage_metadata_test drive_start_stop_test orphan_footage_test capture_cadence_test letter_test tender_determinism_test storage_commit_test stalled_body_test
       ui_text_test routing_test nh_test gis_failure_test footage_test"

fail=0
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
