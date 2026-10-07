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
//
// `tick` runs on every warm event and never on a request: it re-reads what the service
// keeps in memory for a short while (the map rows, the impact period, a town's tender
// index), so a person arriving after a quiet spell is answered from memory.
export function createWarmHandler({ service, warm, tick = null, logger = console }) {
  const failed = (event) => (error) => {
    logger.error(JSON.stringify({ event, error_message: String(error?.message || error).slice(0, 300) }));
  };
  const warmed = Promise.resolve().then(warm).catch(failed("warm_failed"));
  return async (event, context) => {
    await warmed;
    if (event && event.warm === true) {
      if (tick) await Promise.resolve().then(tick).catch(failed("warm_tick_failed"));
      return { warmed: true };
    }
    return service(event, context);
  };
}
