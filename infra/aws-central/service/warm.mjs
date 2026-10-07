// Start-up reads, and the scheduled event that keeps one instance started.
//
// The first lookup on a new function instance read the ownership layers (38 MB), the
// ward polygons and a street tile inside the request: 580 to 664 ms in geo.resolve on
// 7 Oct 2026 against 11 to 100 ms on a warm instance. `warm` does those reads once per
// instance, started when the module loads. A request waits for it (it would have paid
// for the same reads itself); the scheduled {"warm": true} event from the stack's
// WarmSchedule rule exists only to make an instance do it before a person arrives. That
// event is not a request: it reaches no route, writes no request log line and counts in
// no metric.
export function createWarmHandler({ service, warm, logger = console }) {
  const warmed = Promise.resolve().then(warm).catch((error) => {
    logger.error(JSON.stringify({
      event: "warm_failed", error_message: String(error?.message || error).slice(0, 300),
    }));
  });
  return async (event, context) => {
    await warmed;
    if (event && event.warm === true) return { warmed: true };
    return service(event, context);
  };
}
