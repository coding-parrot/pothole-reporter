# Pothole Reporter

An independent Android app for potholes, garbage, and open or damaged manholes. **Drive**
detects visible road damage; **Photo** lets the user choose the issue and prepares evidence
for an official complaint channel. Reports remain on the phone, and nothing is filed
automatically. There is no project-operated backend or account system.

<a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>

<sub>Example detected by the app. Select the thumbnail for the full-size photo.</sub>

## Coverage

| Area | Current scope |
| --- | --- |
| National Highways | Operational NH/NE carriageways mapped across India in the pinned 20 August 2026 OpenStreetMap extract. A match goes to Rajmargyatra/1033; the maintaining agency is not guessed. |
| Delhi | Full Delhi NCT; the wider NCR is excluded. |
| Maharashtra | The current Mumbai Metropolitan Region (MMR), including its rural extent, and Pune Municipal Corporation (PMC). PCMC is excluded. |
| West Bengal | Kolkata Municipal Corporation (KMC) only; Howrah, Bidhannagar/Salt Lake, New Town, and neighbouring bodies are excluded. |
| Karnataka | Road-damage routing for supported urban local bodies with a published recipient. Garbage and manhole handoff is enabled only for the five Bengaluru city corporations through Sahaaya 2.0; other Karnataka bodies fail closed for those categories. |
| Tamil Nadu | Greater Chennai Corporation (GCC) limits only; the wider metropolitan area and neighbouring urban bodies are excluded. |
| Telangana | The official 2,053 km² Telangana Core Urban Region around Hyderabad, checked live against Telangana GIS. Secunderabad Cantonment is excluded. My Cure is offered without guessing which of the three 2026 city corporations owns the issue. |
| Gujarat | A reviewed 48-ward Ahmedabad footprint (439.397 km²). Wider AUDA is excluded; available licensed geometry does not prove that every recent outer AMC expansion is included. |

Boundaries suggest a complaint route; they do not prove who owns or maintains a road.
The user must review and complete every complaint in the offered official app, portal,
WhatsApp, dialler, share sheet, or email client. Nothing is submitted automatically.

## How it works

- **Drive** shows a live road view and counters. Its Android foreground service keeps scanning—and, when explicitly enabled, recording silent local video—under a truthful persistent notification while Maps or a call is open.
- **Photo** offers **Pothole**, **Garbage**, or **Manhole**. Pothole photos use AI; the
  other two are explicit user reports and are not sent to OpenAI.
- OpenAI vision assesses visible potholes, failed patches, breakup, ruts, and depressions.
- Nearby repeat observations are grouped into one report; Debug mode retains each one.
- The app preserves the photo, coordinates, time, category, complaint draft, and official
  reference ID locally. A genuinely temporary routing-data failure can be retried later;
  permanent boundary/category refusals require a new report or added coverage.
- Garbage and manhole handoffs are enabled for MMR, PMC, KMC, Delhi NCT, the five
  Bengaluru city corporations, GCC Chennai, Hyderabad CURE, and the reviewed Ahmedabad
  footprint. Road damage keeps its road-specific channel where one exists.
- English, Kannada, Marathi, and Bengali are supported.

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
needed, it downloads a versioned state pack or the relevant 2° National Highway tile from
this project's GitHub Pages site. Highway matching runs before municipal routing. The
101 highway tiles total about 18 MB, but only the relevant tile is downloaded and cached;
they do not enlarge the APK. The optional Karnataka tender pack is downloaded only for
eligible contract matching.

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
4. Review the image, location, authority, wording, and any probable contract match before
   choosing an external complaint channel.

## Important limits

- AI can miss damage or produce false positives. No field-validated accuracy percentage is
  claimed.
- Android may temporarily take the camera for a video call or another higher-priority app;
  Drive pauses camera sampling and resumes automatically. Normal audio calls do not require
  the camera. Device-specific battery managers can still stop long-running services.
- Selected road-damage images and the user's API key go directly to OpenAI. Garbage and
  manhole photos do not. Exact coordinates go to OpenStreetMap Nominatim; Karnataka
  locations query Karnataka GIS, and Hyderabad coverage queries Telangana GIS.
- Contract matching is optional and available only for eligible Karnataka routes. A match
  is not proof that a contractor or warranty applies.
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
