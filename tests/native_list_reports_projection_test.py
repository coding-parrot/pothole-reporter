#!/usr/bin/env python3
"""Home asks the native store for its reports on every render. It must not read photos.

listReports walked getAllIds() and called getById() per row. getById is SELECT *, so
every return to Home pulled each report's thumbnail and full evidence JPEG through
SQLite, only for reportToJson to drop both and emit has_photo. The evidence image is
fetched separately (getReportPhoto) for the one report the tester opens.

This suite checks the listing path in source: one blob-free query, with has_photo
computed in SQL. ReportListingTest (androidTest) runs that query on a device against
rows with real blobs.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WIRED = ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter"
PLUGIN = (WIRED / "bridge/DriveModePlugin.kt").read_text()
DAO = (WIRED / "db/dao/ReportDao.kt").read_text()
INSTRUMENTED = (ROOT / "android-app/android/app/src/androidTest/java/com/gauravsen/potholereporter"
                / "db/ReportListingTest.kt")
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


start = PLUGIN.index("fun listReports(")
body = PLUGIN[start:PLUGIN.index("@PluginMethod", start)]
check("listReports reads no full report row (no getById or getAll)",
      "getById(" not in body and "getAll(" not in body)
check("listReports uses the listing projection", "listForHome()" in body)

match = re.search(r'@Query\("""(.*?)"""\)\s*suspend fun listForHome\(\): List<ReportListing>', DAO, re.S)
check("ReportDao has a listForHome query returning ReportListing", bool(match))
if match:
    select = match.group(1)
    columns = select[select.index("SELECT") + 6:select.index("FROM reports")]
    bare = re.sub(r"\(photo IS NOT NULL AND length\(photo\) > 0\) AS has_photo", "", columns)
    check("the query selects neither photo nor photo_full", not re.search(r"\bphoto(_full)?\b", bare))
    check("the query computes has_photo in SQL", "AS has_photo" in columns)
    check("the query keeps newest first", "ORDER BY id DESC" in select)

listing = DAO[DAO.index("data class ReportListing"):] if "data class ReportListing" in DAO else ""
listing = listing[:listing.find("\n)\n")]
check("ReportListing carries no ByteArray", bool(listing) and "ByteArray" not in listing)
check("the listing path is exercised on a device by ReportListingTest", INSTRUMENTED.exists())

if failures:
    print(f"FAIL {len(failures)} native listReports check(s)")
    sys.exit(1)
print("native listReports: one blob-free query, has_photo from SQL")
