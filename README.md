# Pothole Reporter

An independent Android app for potholes. **Drive** detects visible potholes while the
phone shows Maps or a call; one tap on **Photo** opens the camera for a pothole check and
prepares evidence for an official complaint channel. Reports remain on the phone, and
nothing is filed automatically. There is no project-operated backend or account system.

Current release: [v1.36.5](https://github.com/coding-parrot/pothole-reporter/releases/tag/v1.36.5)

<p>
  <a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>
  <a href="docs/coverage-overview.svg"><img src="docs/coverage-overview.svg" width="280" alt="Pothole Reporter nationwide India coverage overview"></a>
</p>

<sub>Example detection and current coverage. Map boundary: [DataMeet India community, CC0](https://github.com/datameet/maps/blob/5ed214bf77788f99066e3542cccd4a52cb042896/Country/india-composite.geojson), following the Survey of India standard; no government endorsement. Select either image to enlarge.</sub>

## Coverage

| Area | Current scope and handoff |
| --- | --- |
| National Highways | Potholes only, on operational NH/NE carriageways mapped in the pinned 20 August 2026 OpenStreetMap extract. Matches open Rajmargyatra/1033; the maintainer is not guessed. |
| All 28 states + 8 Union Territories | A confidently contained coordinate can use a conservative official state/UT grievance handoff. The complete GPS-accuracy circle must stay inside a checksum-pinned boundary; border ambiguity fails closed. |
| Exact city/authority routes | Reviewed municipal, metropolitan, and authority-specific routes remain preferred wherever available. Nationwide fallback never replaces a more specific route. |
| 50 largest population centres | All 50 from Census 2011 A-04(I). Existing reviewed/statewide routes stay preferred; 8 centres require both a conservative coordinate envelope and an exact structured city/state match before offering a neutral official grievance channel. Markers do not claim complete Urban Agglomeration boundaries. |
| Delhi | Full Delhi NCT. Road damage uses PWD Sewa. NCR places outside NCT never inherit Delhi's route; Noida and Ghaziabad can qualify through the separate Uttar Pradesh route, while Faridabad retains its top-50 neutral route. |

National Highway routing runs first, followed by exact city/authority routes, then the
state/UT handoff. Boundaries suggest where to start a complaint; they do not prove who
owns or maintains a road. The user must review and complete every complaint in the
offered official app, portal, WhatsApp, dialler, share sheet, or email client. Nothing is
submitted automatically.

## How it works

- **Drive** shows a live road view and counters while Maps or a call is open. Optional silent recording pairs local video with a sharper 720p frame at most every two seconds, keeps only one raw burst waiting for live AI, and can retry unfinished frames after the drive with nearby before/after context.
- A selectable 15/30/60/90-minute active-time battery limit stops Drive automatically; paused time does not count. The default is 30 minutes.
- On a new install, **Settings** appears first so the API key and capture preferences are
  configured before Drive and Photo are shown. **Photo** is one tap and pothole-only.
- OpenAI vision returns only **Pothole: Yes/No**, never a user-facing confidence score.
  A Yes requires a localized cavity, distinct broken rim, visible material loss, usable
  imagery, and consistent Drive views. An actively used temporary traffic surface is
  eligible only with the same cavity persisting across the burst; gravel texture, ruts,
  broad breakup, puddle ambiguity, construction beds, and speed breakers are No.
- Each decision records the surface type and strict defect type. Accepted potholes may
  receive a small/medium/large **app visual estimate**; physical dimensions remain unknown
  without a field measurement, and the estimate is labelled with its provenance and low
  measurement confidence. It is not an official municipal size classification.
- Nearby repeat observations are grouped into one report; Debug mode retains each one.
- On a later live drive, **Fixed** requires a separate before/after AI check that clearly
  sees the same footprint covered by completed, intact repair material. Probable,
  obstructed, mismatched, or merely clean-looking views remain open for review.
- The app preserves the photo, coordinates, time, category, complaint draft, and official
  reference ID locally. A genuinely temporary routing-data failure can be retried later;
  permanent boundary/category refusals require a new report or added coverage.
- Pothole handoffs are enabled in all 28 states and 8 Union Territories, with a
  road-specific channel where one exists.
- English, Kannada, Marathi, and Bengali are supported.

### Complaint output

- BMC and the Bengaluru Central, East, North, South, and West city corporations have
  authority-specific intake profiles. BDA is used only when separate evidence explicitly
  identifies BDA responsibility; location inside Bengaluru is not enough.
- Complaint intake and road ownership are separate fields. A boundary can select where to
  start a complaint without claiming that the same authority owns or maintains the road.
- Email, short WhatsApp, and copyable portal fields always retain coordinates, map link,
  and routing. Karnataka, NHAI/MoRTH/NHIDCL, PMGSY, and State/UT procurement records may
  produce a local **research lead**, but it is not included in complaint copy. Tender and
  contractor details appear only when official evidence verifies the exact road segment,
  carriageway scope, award, road owner, and active DLP/maintenance responsibility. If any
  link is missing or ambiguous, both are omitted.

### National Highway routing

- Runs before municipal routing anywhere in India.
- Uses 101 checksum-pinned tiles containing 680 NH/NE references from the pinned
  20 August 2026 OpenStreetMap India extract.
- Refuses weak GPS, conflicting direction, nearby different highway references, missing
  tiles, and altered data instead of guessing.
- Opens Rajmargyatra or 1033 and asks the official service to identify the maintaining
  NHAI, NHIDCL, BRO, or State PWD unit.

## Downloaded data packs

The APK contains the app and small manifests, not the large reference datasets. When
needed, it downloads a versioned state/UT pack or the relevant 2° National Highway tile
from this project's GitHub Pages site. The 101 highway geometry tiles total about 18 MB,
the 33 current National Highway research packs contain 1,859 records in 1,805,723 bytes,
the 36 PMGSY State/UT packs contain 17,717 records in 3,075,718 bytes, and the 34 official
State/UT notice packs contain 5,310 open notices in 4,225,170 bytes. Only the relevant files
are downloaded and cached. These remote packs do not enlarge the APK; the three manifests
add only about 100 KB to a future build. The Play closed-test binary already submitted to
Google remains unchanged. All inputs are filtered for explicit carriageway work, so
drain-, footpath-, sewer-, utility-, consultancy-, and roadside-plantation-only work is excluded even
when its title contains a road name.

Every downloaded pack is checked byte-for-byte against a checksum pinned in the app before
it is used, then cached locally. Missing, malformed, or altered required routing, contact,
or highway data makes authority routing fail closed—the app does not guess or reuse
unverified data.
If an optional contract or procurement pack is unavailable, the report continues without
a candidate. A verified cached pack is available after its first successful download,
although detection, geocoding, and relevant GIS checks still need their respective network
services. On a subsequent pack use, caches past their unused limits are pruned
automatically; **Delete all app data** removes the entire pack cache immediately.

A pack request contains no report, photo, or exact coordinates. GitHub Pages can receive
the device's IP address, connection metadata, and requested pack URL; the URL reveals the
selected state or an approximate 2° highway tile. See the
[privacy policy](https://coding-parrot.github.io/pothole-reporter/privacy.html).

## Install

1. Install the APK attached to the
   [latest release](https://github.com/coding-parrot/pothole-reporter/releases/latest).
2. On first launch, enter your OpenAI API key in Settings.
   Allow camera and location access; Drive also needs notification access.
3. Capture while safely stopped, or securely mount the phone before starting **Drive**.
4. Review the image, location, authority, wording, and contract-verification status before
   choosing an external complaint channel.

## Important limits

- AI can miss damage or produce false positives. No field-validated accuracy percentage is
  claimed.
- Android may temporarily take the camera for a video call or another higher-priority app;
  Drive pauses camera sampling and resumes automatically. Normal audio calls do not require
  the camera. Device-specific battery managers can still stop long-running services.
- Selected pothole images and the user's API key go directly to OpenAI. Exact coordinates
  go to OpenStreetMap Nominatim; Karnataka
  locations query Karnataka GIS, and exact Hyderabad CURE checks query Telangana GIS.
  State/UT containment is checked locally against downloaded, checksum-pinned ODbL
  boundaries for all 28 states and 8 Union Territories.
  The 8 structured-city routes require Nominatim's city/municipality and state fields;
  a broad envelope or stale place label alone cannot select a route.
- Contract matching is optional and incomplete. The 26 August 2026 National Highway scan
  read 3,258 MoRTH and 529 NHIDCL rows, normalized 1,950 current/open records
  (1,939 + 11), and published the 1,859 with a mapped NH/NE reference. PMGSY retained
  17,717 recent,
  source-reported **In Progress** agreements from 211,007 rows; agreement details are
  verified as source fields, but award, contractor, exact segment, and DLP are not.
  The official State/UT notice catalog scanned 69,754 rows from 35 public feeds, rejected
  64,444 through fail-closed scope/current-record checks, and retained 5,310 notices in 34
  jurisdiction packs. It combines GePNIC with dedicated Bihar, Chhattisgarh, Gujarat,
  Telangana, and Lakshadweep adapters. Andhra Pradesh remains blocked by undocumented
  client-encrypted listing state; KPPP remains a separate Karnataka supplement.
- Research leads never prove that every tender or road is covered. Open notices are not
  awards. Exact segment, scope, award, contractor, owner, warranty, and active DLP require
  authoritative evidence; publication date proves none. The current public packs contain
  no record satisfying that complete chain, so complaints currently name no contractor.
- National Highway coverage follows mapped NH/NE geometry, not the legal road register.
  Parallel roads, junctions, weak GPS, missing tiles, and altered data fail closed.
- The project is not affiliated with or endorsed by a government body or data provider.

Read [data sources and limits](https://coding-parrot.github.io/pothole-reporter/sources.html)
for exact coverage, provenance, and known gaps.

## Build and test

```bash
./tools/build-apk.sh
./tests/run-all.sh
# Explicit live-service checks: RUN_LIVE_TESTS=1 ./tests/run-all.sh
```

## License

Code is MIT; data retains its source licences and terms. See [LICENSE](LICENSE) and
[data sources and limits](https://coding-parrot.github.io/pothole-reporter/sources.html).
