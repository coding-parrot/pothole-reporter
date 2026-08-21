# Where the app's data and judgments come from

This document records provenance, transformations, and limits. The app combines a
user's camera and phone location with government reference data, OpenStreetMap data,
local matching rules, and AI-generated judgments. A source record is not proof that a
particular defect, authority, or contract match is correct. The user must review the
photo, routing, and any probable contract match before sending a complaint.

## Greater Mumbai complaint handoff

The Mumbai flow prepares evidence for the citizen to submit through BMC's own process.
It does not call a BMC complaint-write API, use BMC credentials, bypass OTP, or read a
complaint status. Opening Pothole QuickFix is recorded only as “handoff opened”; Share,
WhatsApp, and Call also never mark the report submitted. The app requires the user to enter
the official grievance ID returned by BMC before marking the local report submitted.

**Official sources checked on 21 August 2026:**

- [Pothole QuickFix on Google Play](https://play.google.com/store/apps/details?id=com.bmc.potholequickfix)
  identifies Brihanmumbai Municipal Corporation as the developer and describes OTP login,
  geo-watermarked photo evidence, grievance tracking, and reopening within 24 hours.
- BMC's MARG page at `https://marg.mcgm.gov.in/MARG/welcomePage.html` provides mobile/OTP
  login, grievance-ID search, and geotagged-image upload. It is an official reference, not
  an API integrated by this app.
- BMC's pothole dashboard at
  `https://marg.mcgm.gov.in/BMC_C_Pothole_Dashboard/` identifies Pothole QuickFix,
  WhatsApp Chatbot, 1916, and Twitter as grievance sources and includes an “other agencies”
  category. Dashboard availability or a zero count is not evidence that a handoff succeeded.
- BMC's IT page at `https://portal.mcgm.gov.in/irj/portal/anonymous/director-it` publishes
  the MyBMC WhatsApp chatbot number **+91 89992 28999**. Its civic-complaints page at
  `https://portal.mcgm.gov.in/irj/portal/anonymous/qlCLCComp` publishes **1916** for roads
  and traffic complaints. The app uses `https://wa.me/918999228999` and `tel:1916` only
  after the user selects those actions.

Greater Mumbai coverage is inferred only when OpenStreetMap Nominatim returns Maharashtra
and either Mumbai City district or Mumbai Suburban district. A ward code, when present, is
parsed from the OpenStreetMap address and shown as a suggestion to verify in the official
service. The app does not bundle BMC ward polygons or establish which road agency owns the
carriageway.

“Open Pothole QuickFix” opens the installed official app, with its Google Play listing as
a fallback. “BMC WhatsApp” passes
prefilled text to WhatsApp, “Call 1916” opens the dialler, and “Share evidence” invokes
Android's share sheet with compressed image evidence and report text. The citizen must
review and complete the complaint in the chosen external service.

BMC's website policy at
`https://portal.mcgm.gov.in/irj/portal/anonymous/privacy?guest_user=english` requires prior
permission for linking, framing, or caching its website and says website graphics should be
presumed copyrighted. The app therefore does not embed or frame BMC pages, copy BMC marks,
or bundle BMC GIS data. Written permission is required before adding those integrations.

## Road contracts (42,283 rows)

**Source: KPPP, the Karnataka Public Procurement Portal, Government of Karnataka.**
<https://kppp.karnataka.gov.in>

This is the state's own e-procurement portal. The app uses the same awarded-tender
search that the portal's public website uses, which needs no login, no API key and no
registration. Reproduce it:

```bash
curl -s -X POST "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service/works/search-eproc-tenders?page=0&size=1000&order-by-tender-publish=true" \
  -H "content-type: application/json" \
  -H "Referer: https://kppp.karnataka.gov.in/" \
  -H "Post: CONTRACTOR-EPROC-CONTRACTOR" \
  -d '{"category":"WORKS","status":"AWARDED"}' -D - -o page0.json | grep -i x-total-count
```

That header reports the total the portal holds: **98,009 awarded works** at the time of
writing. `tools/pull-kppp.py` walks those pages and keeps the road-related ones by
matching the work title, which is why the bundle is 42,283 rows rather than all 98,009.

**Verification done on 19 Aug 2026:** of the 1,000 rows on the portal's first page,
**341 appear in the bundled file, byte-identical on title, publication date and
location**. The rest of that page is non-road work the sweep deliberately drops.

Fields taken: `tenderNumber`, `description` (the work title), `locationName`,
`publishedDate`. Nothing is edited beyond truncating long titles.

## Contractor names (1,124 of those rows)

**Source: the public-domain snapshot at
<https://bengaluru-road-contracts.pages.dev>, whose own source is KPPP.**

The portal's *search* results do not include the winning bidder; only the per-tender
full view does. So contractor names come from that pre-existing snapshot of Bengaluru
contracts. Outside Bengaluru the complaint names the tender and states plainly that no
winning bidder is recorded. It never guesses a company name.

## Which officer receives the complaint (182 local bodies)

**Sources: the district NIC sites of the Government of Karnataka, and the bodies' own
sites on the Municipal Reforms Cell domain.**
Pattern: `https://<district>.nic.in/en/public-utility-category/municipality/` and
`https://<name>city.mrc.gov.in`.

Every entry in [`data/karnataka-bodies.json`](../data/karnataka-bodies.json) carries a
`source` field naming the page its address was read from. The 18 city corporations were
each checked twice, by separate passes, because they carry the most traffic. A body whose
address could not be found on a government page is **not** in the file, and the app
refuses to name a recipient for it rather than guessing.

## Which body owns the road

**Source: KGIS, run by KSRSAC, the Karnataka State Remote Sensing Applications Centre.**

The app asks the state's own GIS which local body's boundary contains the pothole:

```bash
curl -s "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query?geometry=76.6394,12.2958&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=KGISTownName,Town_Type,LGD_TownCode&returnGeometry=false&f=json"
```

Returns `MYSURU`, type `CC`, LGD code `252045`. That layer holds exactly 319 polygons,
matching the state's 319 urban local bodies. Rural points fall through to the gram
panchayat layer.

The officer directory is keyed on **LGD_TownCode**, the Local Government Directory code
issued by the Ministry of Panchayati Raj, so a body is identified by a national
identifier rather than by matching a place name.

## Street address

**Source: OpenStreetMap, via Nominatim.** It turns coordinates into a street name and
pincode for the complaint. For Mumbai, its state and district fields also decide whether
the Mumbai handoff is offered, and a ward phrase may be parsed as a non-authoritative
suggestion. The coordinates themselves come from the phone's GPS and are printed in the
complaint alongside a map link, so the location can be checked independently.

## What is generated or inferred

The pothole verdict, its size and the one-line description are produced by an AI vision
model looking at the photograph, and the app says so. They are a judgement about a
photograph, not a record, which is why the photograph is always attached: the officer can
disagree by looking.

A probable contract match is also a judgment, not a procurement record. The app first
builds a deterministic local shortlist from address words and contracts indexed to the
same local body, then asks an AI model whether one candidate clearly covers that road or
locality. A confidence threshold can reject weak matches, but it cannot turn an accepted
match into proof. The complaint asks the receiving officer to verify it against the
tender documents.

The warranty status is **inferred** from how recently the tender was published, because
award records carry no defect liability period. The complaint states it as a possibility
and asks the officer to verify against the tender documents. That wording is deliberate
and should not be strengthened.

## Known limits

- Mumbai coverage is a geocoder-based city check, not a BMC ward or road-ownership lookup.
  OpenStreetMap can be missing, stale, or wrong, and the app cannot distinguish BMC roads
  from roads maintained by MMRDA, MSRDC, PWD, NHAI, or another agency.
- There is no automatic BMC filing, status sync, or cross-user report database. Evidence
  remains local until the citizen deliberately opens an external handoff.
- 137 of Karnataka's 319 local bodies have no address in the file, because their district
  pages publish none. Those reports refuse to route.
- 41,159 of the 42,283 contracts have no winning bidder recorded, for the reason above.
- Of the 42,283 bundled rows, 18,972 are municipal (DMA or legacy BBMP) records. Only
  13,577 are currently indexed to a supported body and can enter the app's contract
  shortlist. The remaining municipal rows are unresolved or belong to bodies without a
  published address; the 23,311 non-municipal rows belong to agencies such as PWD,
  panchayats, and irrigation departments and are not candidates for a municipal complaint.
- The road-work filter matches on title keywords, so the candidate pool is inclusive by
  design; the confidence gate on the match is what keeps weak candidates out of a
  complaint.
- Contracts are a snapshot. Re-run `tools/pull-kppp.py` to refresh.
