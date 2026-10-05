"""Fail-closed checks shared by device harnesses and their regression tests."""


def drive_failures(offered, alerts, console, errors):
    failures = []
    if not offered:
        failures.append("Post-drive analysis was not offered")
    summaries = [a for a in alerts if a.startswith(("Footage analysed", "Could not finish"))]
    if not summaries or not summaries[-1].startswith("Footage analysed"):
        failures.append("Post-drive analysis did not complete")
    if any("incomplete" in a.lower() or "writing stopped" in a.lower() for a in alerts):
        failures.append("Evidence export was incomplete")
    if errors:
        failures.append("Uncaught page errors")
    if any(c.startswith(("error:", "assert:")) for c in console):
        failures.append("Console errors")
    return failures
