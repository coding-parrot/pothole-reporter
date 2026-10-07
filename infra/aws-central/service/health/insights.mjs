// Runs one Logs Insights query to its end and returns its rows as plain objects.
//
// `logs` is the three calls it needs, shaped like the AWS SDK's (startQuery,
// getQueryResults, stopQuery), so the scheduled function passes the SDK client and the
// command-line script passes the aws CLI. A query is charged by the bytes it scans,
// which only its last answer says; `onScanned` is told, so a run can log what it cost.
export function createInsights({ logs, logGroupName, pollMs, patienceMs, now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), onScanned = () => {} }) {
  return async function insights(queryString, hours) {
    const endTime = Math.floor(now() / 1000);
    const { queryId } = await logs.startQuery({ logGroupName, startTime: endTime - hours * 3600, endTime, queryString });
    const giveUpAt = now() + patienceMs;
    for (;;) {
      const answer = await logs.getQueryResults({ queryId });
      if (answer.status === "Complete") {
        onScanned(Number(answer.statistics?.bytesScanned) || 0);
        return answer.results.map((row) => Object.fromEntries(row.map(({ field, value }) => [field, value])));
      }
      if (["Failed", "Cancelled", "Timeout"].includes(answer.status)) throw new Error(`Logs Insights ${answer.status}`);
      if (now() >= giveUpAt) {
        // A query left running is still charged for what it scans.
        await Promise.resolve().then(() => logs.stopQuery({ queryId })).catch(() => {});
        throw new Error("Logs Insights query did not complete");
      }
      await sleep(pollMs);
    }
  };
}
