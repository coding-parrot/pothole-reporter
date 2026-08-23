# Google Play listing copy

Prepared 23 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (74/80 characters)

```text
Report potholes, garbage and open manholes in supported Indian cities.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting potholes, garbage, and open or damaged manholes and preparing an editable civic complaint. Take one photo while safely stopped and choose the issue. For road damage, you can also securely mount the phone and use foreground Drive Mode while Maps or a call is on screen. AI assesses selected road images for pothole cavities, failed patches, breakup, ruts, and depressions. Garbage and manhole reports are explicitly confirmed by you and do not use AI. Reports stay on your device for review.

Coverage
• Mapped operational National Highways and National Expressways across India. Matches use Rajmargyatra/1033 before municipal routing; the maintaining agency is not guessed.
• Delhi NCT, not the wider NCR. PWD Sewa is offered as a cross-agency handoff; this does not claim PWD owns the road.
• The current Mumbai Metropolitan Region. Valid local polygons can suggest a body; rural, overlapping, and unsupported MMR locations use Aaple Sarkar.
• Pune Municipal Corporation limits. PCMC is excluded.
• Kolkata Municipal Corporation limits. Howrah, Bidhannagar/Salt Lake, New Town, and neighbouring bodies are excluded.
• Road-damage routing for supported Karnataka urban local bodies with a published recipient and eligible road classification. Garbage and manhole handoff is enabled only for the five Bengaluru city corporations through Sahaaya 2.0.
• Greater Chennai Corporation limits, not the wider Chennai Metropolitan Area or neighbouring urban bodies.
• The official 2,053 sq km Telangana Core Urban Region around Hyderabad, checked live against Telangana GIS. Secunderabad Cantonment is excluded. My Cure is offered without assigning the point to Greater Hyderabad, Cyberabad, or Malkajgiri Municipal Corporation after the 2026 reorganisation.
• A reviewed 48-ward Ahmedabad footprint covering 439.397 sq km. Wider AUDA is excluded. The available licensed geometry does not prove that every recent outer AMC expansion is included.

Locations outside these routes are saved locally but are not addressed to an authority.

Reporting
• Photo offers Pothole, Garbage, and Manhole. Each saved report includes its category, photo, coordinates, capture time, editable complaint wording, and suggested official route.
• Pothole reports use road-specific channels where available. Garbage and manhole handoffs are enabled for MMR, PMC, KMC, Delhi NCT, the five Bengaluru city corporations, GCC Chennai, Hyderabad CURE, and the reviewed Ahmedabad footprint. An unverified category or recipient fails closed instead of being guessed.
• Nearby repeat observations are grouped into one draft.
• A saved but unrouted report keeps its evidence. Temporary routing-data failures can be retried; a permanent boundary/category refusal cannot be changed by retrying the same coordinates.
• The app can open a published app, portal, WhatsApp chat, dialler, share sheet, or editable email draft.
• Nothing is submitted automatically. The app does not log in, bypass OTP, press Send, or read complaint status. Complete the complaint in the external service and record its reference ID when required.

Important limits and data use
• AI can miss damage or produce false positives. Review the photo, wording, location, and recipient.
• A boundary or place match suggests a route; it does not prove road ownership.
• Probable contract matching is available only for eligible Karnataka routes and is not proof of responsibility or warranty.
• Pothole AI and Drive require camera and foreground location permission, internet, and your own billed OpenAI API key. Garbage and manhole reporting does not require an OpenAI key, but location routing and external handoffs still need connectivity.
• Selected resized road-damage images and API requests go directly to OpenAI. User-confirmed garbage and manhole photos do not. Precise coordinates go to OpenStreetMap Nominatim; Karnataka points query Karnataka GIS, and Hyderabad coverage checks query Telangana GIS.
• Regional routing/contact packs, 2° National Highway tiles, and the optional Karnataka tender pack download from this project's GitHub Pages when relevant. Complete downloads are SHA-256 verified and cached locally. A required routing-data failure stops authority routing; an optional tender-pack failure only omits contract context. Requests identify a state or approximate tile but contain no report, photo, or exact coordinates.
• MMR, PMC, KMC, Delhi NCT, and GCC Chennai boundary checks run locally. Ahmedabad uses a reviewed local ward union. Hyderabad sends a small GPS-accuracy envelope to the official Telangana GIS jurisdiction service and fails closed if that service cannot verify coverage.
• Choosing an external handoff transfers selected data or connection metadata under that provider's policy.
• Drive recording is optional and off by default. Mount the phone before moving.
• The app is not affiliated with or endorsed by any government body.

Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Government-information source directory and exact limits, with direct official links: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.22.0 / version code 40)

```text
Photo can now prepare pothole, garbage, and open-manhole complaints across MMR, PMC, KMC, Delhi NCT, the five Bengaluru city corporations, GCC Chennai, Hyderabad CURE, and the reviewed Ahmedabad footprint. Temporary routing failures can be retried; permanent boundary/category refusals stay closed. Drive Mode remains road-damage-only and continues under its visible notification while Maps or a call is open.
```

## Play Console fields

- Recommended category: **Tools**.
- Ads declaration: **No**, provided no advertising SDK or ad content is added before release.
- Privacy policy URL:
  `https://coding-parrot.github.io/pothole-reporter/privacy.html`
- Support website: `https://github.com/coding-parrot/pothole-reporter/issues`
- Data-source page:
  `https://coding-parrot.github.io/pothole-reporter/sources.html`
- Public support email: **contact@aiengg.dev**.

Do not use government marks or describe Pothole Reporter as “official,” a “government
app,” or affiliated with a civic body. Do not claim guaranteed detection, automatic filing,
a verified pothole, road ownership, or a measured accuracy percentage without independent
evidence.
