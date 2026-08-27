#!/usr/bin/env bash
# Local checks that guard shipped behaviour. Some mocked checks still read the test key
# from .env, but no live service is contacted unless RUN_LIVE_TESTS=1 is explicit.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python3
if [ ! -x "$PY" ]; then
  # Git worktrees do not copy ignored virtualenvs. Reuse the main checkout's venv when
  # available so this exact suite remains runnable on release branches/worktrees.
  COMMON_GIT_DIR=$(git rev-parse --git-common-dir 2>/dev/null || true)
  MAIN_CHECKOUT=$(cd "$COMMON_GIT_DIR/.." 2>/dev/null && pwd || true)
  [ -x "$MAIN_CHECKOUT/.venv/bin/python3" ] && PY="$MAIN_CHECKOUT/.venv/bin/python3" || PY=python3
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

LOCAL_TESTS="unit_test android_release_optimization_test tender_scope_test tender_source_registry_test gepnic_tender_crawler_test gepnic_road_notice_pack_builder_test bihar_road_tender_puller_test chhattisgarh_chips_road_tender_puller_test gujarat_nprocure_road_tender_puller_test lakshadweep_road_tender_puller_test telangana_road_tender_puller_test andhra_pradesh_road_tender_source_test kppp_road_award_puller_test emarg_road_contract_puller_test pmgsy_road_agreement_puller_test pmgsy_road_agreement_pack_builder_test national_highway_contracts_test tender_contract_pack_builder_test catalog_pack_pruner_test highway_contract_matching_test road_notice_matching_test road_agreement_matching_test contract_attribution_gate_test civic_issue_test state_pack_validation_test state_pack_test national_highway_pack_test national_highway_routing_test pages_assets_test coverage_map_test punjab_routing_test tamil_nadu_routing_test andhra_pradesh_routing_test telangana_routing_test karnataka_statewide_routing_test kerala_statewide_routing_test uttar_pradesh_routing_test chhattisgarh_routing_test rajasthan_routing_test goa_mp_bihar_odisha_routing_test remaining_india_routing_test top50_routing_test municipal_city_routing_test home_actions_test first_run_settings_test mumbai_routing_test maharashtra_routing_test kolkata_routing_test delhi_routing_test submission_truth_test mumbai_ui_test kolkata_ui_test delhi_ui_test eval_contract_test persistent_dedupe_test repair_status_test native_bridge_paging_test native_background_drive_test hybrid_drive_contract_test native_keyframe_transaction_test native_duplicate_revisit_contract_test footage_metadata_test drive_start_stop_test orphan_footage_test capture_cadence_test letter_test complaint_profile_test legacy_complaint_copy_test storage_commit_test stalled_body_test
             contribution_map_test native_cleanup_retry_contract_test native_capture_safety_contract_test stored_xss_test privacy_consent_test photo_pothole_only_test delete_all_data_test ui_text_test"
LIVE_TESTS="tender_determinism_test routing_test nh_test gis_failure_test footage_test"
TESTS="$LOCAL_TESTS"
if [ "${RUN_LIVE_TESTS:-0}" = "1" ]; then
  TESTS="$TESTS $LIVE_TESTS"
else
  echo "Live OpenAI/KGIS checks skipped (set RUN_LIVE_TESTS=1 to include them)."
fi

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
