# Google Play listing copy

Prepared 24 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (73/80 characters)

```text
Report potholes, garbage and open manholes across supported Indian areas.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting potholes, garbage, and open or damaged manholes. It saves an editable complaint draft on your phone and suggests a reviewed official route; nothing is filed automatically.

Use Photo while safely stopped. For road damage, mount the phone before moving and use foreground Drive Mode while Maps or a call is on screen. AI assesses selected road images for pothole cavities, failed patches, breakup, ruts, and depressions. Garbage and manhole reports are confirmed by you and do not use AI.

Coverage
• Mapped operational National Highways and National Expressways across India, with Rajmargyatra/1033 offered before municipal routing.
• The full State of Maharashtra. Exact MMR and PMC routes are retained; other Maharashtra points use Aaple Sarkar, with MahaULB offered for urban areas.
• The full State of West Bengal. The exact KMC route stays specific; all other West Bengal points use the neutral West Bengal PGRS handoff, with CMO Grievance as an alternate.
• Delhi NCT, Greater Chennai Corporation, Hyderabad's verified CURE footprint, and a reviewed Ahmedabad 48-ward footprint.
• Road-damage routing for supported Karnataka urban bodies. Garbage and manholes are enabled through Sahaaya 2.0 only in the five Bengaluru city corporations.

Locations outside these routes are saved locally but are not addressed to an authority.

Reporting
• Photo supports Pothole, Garbage, and Manhole. Reports include the category, photo, coordinates, time, editable wording, and suggested official route.
• Garbage and manhole handoffs are enabled throughout Maharashtra and West Bengal and in the supported city footprints above.
• Nearby repeat observations are grouped into one draft.
• The app can open a published app, portal, WhatsApp chat, dialler, share sheet, or email draft.
• You must verify the issue, location, recipient, and wording, then complete submission in the external service.

Important limits and data use
• AI can miss damage or produce false positives. Review the photo, wording, location, and recipient.
• A boundary match suggests a route; it does not prove road ownership, responsibility, or warranty.
• Pothole AI and Drive require camera and foreground location permission, internet, and your own billed OpenAI API key. Garbage and manhole reporting does not require an OpenAI key.
• Selected resized road-damage images and API requests go directly to OpenAI. Precise coordinates go to OpenStreetMap Nominatim; Karnataka and Hyderabad checks may query their official GIS services. Downloaded Maharashtra and West Bengal boundaries are checked on-device.
• Routing and highway packs download from this project's GitHub Pages, are SHA-256 verified, and are cached locally. They contain no report, photo, or exact coordinates.
• Choosing an external handoff transfers selected data or connection metadata under that provider's policy.
• Drive recording is optional, visible through Android's foreground-service notification, and off by default.
• The app is not affiliated with or endorsed by any government body.

Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Government-information source directory and exact limits, with direct official links: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.24.0 / version code 42)

```text
Coverage now spans the full State of West Bengal. The verified KMC route stays specific; every other in-state location uses the neutral West Bengal PGRS handoff, with CMO Grievance as an alternate. The user must select and verify the district or department. Nothing is filed automatically.
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
