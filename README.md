# Pothole Reporter

An independent Android app for potholes, garbage, and open or damaged manholes. **Drive**
detects visible potholes; **Photo** lets the user choose the issue and prepares evidence
for an official complaint channel. Reports remain on the phone, and nothing is filed
automatically. There is no project-operated backend or account system.

Current release: [v1.35.0](https://github.com/coding-parrot/pothole-reporter/releases/tag/v1.35.0)

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
| Delhi | Full Delhi NCT. Road damage uses PWD Sewa; garbage and manholes use CM JanSunwai. NCR places outside NCT never inherit Delhi's route; Noida and Ghaziabad can qualify through the separate Uttar Pradesh route, while Faridabad retains its top-50 neutral route. |

National Highway routing runs first, followed by exact city/authority routes, then the
state/UT handoff. Boundaries suggest where to start a complaint; they do not prove who
owns or maintains a road. The user must review and complete every complaint in the
offered official app, portal, WhatsApp, dialler, share sheet, or email client. Nothing is
submitted automatically.

## How it works

- **Drive** shows a live road view and counters while Maps or a call is open. Optional recording pairs low-resolution silent video with a sharper 720p frame at most every two seconds, keeps only one raw burst waiting for live AI, and can retry unfinished frames after the drive with low-resolution before/after context.
- A selectable 15/30/60/90-minute active-time battery limit stops Drive automatically; paused time does not count. The default is 30 minutes.
- **Photo** offers **Pothole**, **Garbage**, or **Manhole**. Pothole photos use AI; the
  other two are explicit user reports and are not sent to OpenAI.
- OpenAI vision returns only **Pothole: Yes/No**, never a user-facing confidence score.
  A Yes requires a bituminous/asphalt, cement-concrete, mastic-asphalt, or paver-block
  drivable surface, localized cavity, broken rim, visible material loss, usable imagery,
  and consistent Drive views; ambiguous surfaces, unpaved damage, and speed breakers are No.
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
- Garbage and manhole handoffs are enabled in all 28 states and 8 Union Territories.
  Road damage keeps its road-specific channel where one exists.
- English, Kannada, Marathi, and Bengali are supported.

### Complaint output

- BMC and the Bengaluru Central, East, North, South, and West city corporations have
  authority-specific intake profiles. BDA is used only when separate evidence explicitly
  identifies BDA responsibility; location inside Bengaluru is not enough.
- Complaint intake and road ownership are separate fields. A boundary can select where to
  start a complaint without claiming that the same authority owns or maintains the road.
- Email, short WhatsApp, and copyable portal fields always retain coordinates, map link,
  routing, and a contract block. A Karnataka tender match remains a **candidate** until the
  exact road segment, carriageway scope, awarded contractor, and DLP terms are verified.
  Publication date is metadata only and never implies warranty or DLP.

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
needed, it downloads a versioned state/UT pack or the relevant 2° National Highway tile from
this project's GitHub Pages site. Highway matching runs before municipal routing. The
101 highway tiles total about 18 MB, but only the relevant tile is downloaded and cached;
they do not enlarge the APK. The optional Karnataka tender pack is downloaded only for
eligible contract matching. It contains only titles with explicit carriageway work;
drain-, footpath-, sewer-, and other roadside-only contracts are excluded even when a
road name appears as their location.

Every downloaded pack is checked byte-for-byte against a checksum pinned in the app before
it is used, then cached locally. Missing, malformed, or altered required routing, contact,
or highway data makes authority routing fail closed—the app does not guess or reuse
unverified data.
If the optional Karnataka tender pack is unavailable, the report continues without a
contract match. A verified cached pack is available after its first successful download,
although detection, geocoding, and Karnataka GIS still need their respective network
services. On a subsequent pack use, caches past their unused limits are pruned
automatically; **Delete all app data** removes the entire pack cache immediately.

A pack request contains no report, photo, or exact coordinates. GitHub Pages can receive
the device's IP address, connection metadata, and requested pack URL; the URL reveals a
state or an approximate 2° highway tile. See the
[privacy policy](https://coding-parrot.github.io/pothole-reporter/privacy.html).

## Install

1. Install the APK attached to the
   [latest release](https://github.com/coding-parrot/pothole-reporter/releases/latest).
2. For Pothole or Drive, enter your OpenAI API key. Garbage and Manhole do not need it.
   Allow camera and location access; Drive also needs notification access.
3. Capture while safely stopped, or securely mount the phone before starting **Drive**.
4. Review the image, location, authority, wording, and any contract candidate before
   choosing an external complaint channel.

## Important limits

- AI can miss damage or produce false positives. No field-validated accuracy percentage is
  claimed.
- Android may temporarily take the camera for a video call or another higher-priority app;
  Drive pauses camera sampling and resumes automatically. Normal audio calls do not require
  the camera. Device-specific battery managers can still stop long-running services.
- Selected road-damage images and the user's API key go directly to OpenAI. Garbage and
  manhole photos do not. Exact coordinates go to OpenStreetMap Nominatim; Karnataka
  locations query Karnataka GIS, and exact Hyderabad CURE checks query Telangana GIS.
  State/UT containment is checked locally against downloaded, checksum-pinned ODbL
  boundaries for all 28 states and 8 Union Territories.
  The 8 structured-city routes require Nominatim's city/municipality and state fields;
  a broad envelope or stale place label alone cannot select a route.
- Contract matching is optional and available only for eligible Karnataka routes. Only
  explicit carriageway work can enter the matcher; drain-, footpath-, sewer-, utility-, or
  other roadside-only work is rejected. Every match stays a candidate unless road segment,
  scope, award, and DLP are independently verified; publication date proves none of them.
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
