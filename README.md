# Pothole Reporter

An independent Android app that detects visible road damage, groups nearby repeat
observations, and prepares a complaint for the user to review. Reports and stored photos
remain on the phone; selected resized images go directly to OpenAI for analysis, and
complaint evidence leaves only when the user chooses an external handoff. There is no
project-operated backend or account system.

<a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>

<sub>Example detected by the app. Select the thumbnail for the full-size photo.</sub>

## Coverage

| Area | Current scope |
| --- | --- |
| Delhi | Full Delhi NCT; the wider NCR is excluded. |
| Maharashtra | The current Mumbai Metropolitan Region (MMR), including its rural extent, and Pune Municipal Corporation (PMC). PCMC is excluded. |
| West Bengal | Kolkata Municipal Corporation (KMC) only; Howrah, Bidhannagar/Salt Lake, New Town, and neighbouring bodies are excluded. |
| Karnataka | Supported urban local bodies with a published recipient; GIS checks refuse highways, rural roads, unknown road classes, and unsupported bodies. |
| Tamil Nadu | Greater Chennai Corporation (GCC) limits only; the wider metropolitan area and neighbouring urban bodies are excluded. |
| Telangana | A conservative Hyderabad-core outline only. Coverage is partial, the published Secunderabad Cantonment extent is refused, and My Cure is offered without attributing a point to any one of the three 2026 corporations. |
| Gujarat | Ahmedabad only when Nominatim returns an exact structured city or municipality match inside a reviewed relevance envelope. This is not an AMC boundary claim. |

Boundaries suggest a complaint route; they do not prove who owns or maintains a road.
The user must review and complete every complaint in the offered official app, portal,
WhatsApp, dialler, share sheet, or email client. Nothing is submitted automatically.

## How it works

- **Drive** samples the road while the mounted phone remains in the foreground.
- **Photo** analyses one stopped capture.
- OpenAI vision assesses visible potholes, failed patches, breakup, ruts, and depressions.
- Nearby repeat observations are grouped into one report; Debug mode retains each one.
- Reports, photos, optional recordings, and official reference IDs are stored locally.
- English, Kannada, Marathi, and Bengali are supported.

## State data packs

The APK contains the app, not every region's large reference dataset. When needed, it
downloads a versioned routing/contact pack for that state from this project's GitHub Pages
site. Chennai and Hyderabad polygon checks, and Ahmedabad's relevance-envelope check, use
the verified downloaded pack. The optional Karnataka tender pack is downloaded only for
eligible Karnataka contract matching. Adding another state therefore adds a hosted pack
without continually inflating the core APK.

Every downloaded pack is checked byte-for-byte against a checksum pinned in the app before
it is used, then cached locally. A missing, malformed, or altered required routing/contact
pack makes authority routing fail closed—the app does not guess or reuse unverified data.
If the optional Karnataka tender pack is unavailable, the report continues without a
contract match. A verified cached pack is available after its first successful download,
although detection, geocoding, and Karnataka GIS still need their respective network
services. On a subsequent pack use, caches past their unused limits are pruned
automatically; **Delete all app data** removes the entire pack cache immediately.

A pack request contains no report, photo, or exact coordinates. GitHub Pages can receive
the device's IP address, standard connection metadata, and the requested pack URL; because
the URL names a state, it reveals that coarse state. See the
[privacy policy](https://coding-parrot.github.io/pothole-reporter/privacy.html).

## Install

1. Install `PotholeReporter.apk` from the
   [latest release](https://github.com/coding-parrot/pothole-reporter/releases/latest).
2. Enter your OpenAI API key and allow camera and foreground location access.
3. Capture while safely stopped, or securely mount the phone before starting **Drive**.
4. Review the image, location, authority, wording, and any probable contract match before
   choosing an external complaint channel.

## Important limits

- AI can miss damage or produce false positives. No field-validated accuracy percentage is
  claimed.
- Selected images and the user's API key go directly to OpenAI. Exact coordinates go to
  OpenStreetMap Nominatim; Karnataka locations also query Karnataka GIS.
- Contract matching is optional and available only for eligible Karnataka routes. A match
  is not proof that a contractor or warranty applies.
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
