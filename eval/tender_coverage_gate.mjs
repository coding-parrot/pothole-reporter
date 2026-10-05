// A populated table is necessary, not sufficient, for a functioning tender service.
export function coverageFailures(summary) {
  const failures = [];
  if (!(summary.production?.consistent_table_count > 0)) failures.push('Production tender table is empty or unverified');
  const traffic = summary.traffic;
  if (traffic?.resolve_post_requests > 0 && !(traffic.tender_matched_requests > 0)) {
    failures.push('No successful tender matches observed in the audited requests');
  }
  return failures;
}
