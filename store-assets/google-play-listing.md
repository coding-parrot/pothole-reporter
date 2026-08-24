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
Pothole Reporter is an independent Android app for documenting potholes, garbage, and open or damaged manholes. It saves editable complaint drafts on your phone; nothing is filed automatically.

Use Photo while safely stopped. For road damage, mount the phone and use foreground Drive while Maps or a call is on screen. AI assesses selected road images for cavities, failed patches, breakup, ruts, and depressions. Garbage and manhole reports are user-confirmed and do not use AI.

Coverage
• Mapped operational National Highways and National Expressways across India, with Rajmargyatra/1033 offered before municipal routing.
• The full State of Maharashtra. Exact MMR and PMC routes are retained; other Maharashtra points use Aaple Sarkar, with MahaULB offered for urban areas.
• The full State of West Bengal. The exact KMC route stays specific; all other West Bengal points use the neutral West Bengal PGRS handoff, with CMO Grievance as an alternate.
• The full State of Punjab through Connect Punjab, with mSeva offered for urban areas. Chandigarh is outside the Punjab boundary.
• Tamil Nadu statewide: the exact Greater Chennai Corporation route stays preferred; other confidently contained points use neutral Mudhalvarin Mugavari. Puducherry and Karaikal are excluded.
• The full State of Andhra Pradesh through PGRS, with Puramithra offered for urban areas and helpline 1902. Yanam is excluded.
• The full State of Telangana through neutral Prajavani, with Citizen Buddy offered for municipal areas outside Hyderabad. Verified Hyderabad CURE keeps My Cure; Secunderabad Cantonment is excluded from My Cure but can use neutral Prajavani.
• All 50 largest Census 2011 population centres. Existing reviewed/statewide routes stay preferred; 31 routes require a conservative coordinate envelope and exact structured city/state data. Markers are not complete Urban Agglomeration boundaries.
• Delhi NCT and a reviewed Ahmedabad 48-ward footprint.
• Road-damage routing for supported Karnataka urban bodies. Garbage and manholes are enabled through Sahaaya 2.0 only in the five Bengaluru city corporations.

Locations outside these routes are saved locally but are not addressed to an authority.

Reporting
• Photo supports Pothole, Garbage, and Manhole. Reports include the category, photo, coordinates, time, editable wording, and suggested official route.
• Garbage and manhole handoffs are enabled throughout Maharashtra, West Bengal, Punjab, Tamil Nadu, Andhra Pradesh, and Telangana and in all accepted top-50 city routes.
• Nearby repeat observations are grouped into one draft.
• On a precise later live drive, the app can compare the saved damage photo with current views. It marks Fixed automatically only when the same footprint and completed intact repair are both clear; probable or inconclusive results remain open for review.
• The app can open a published app, portal, WhatsApp chat, dialler, share sheet, or email draft.
• You must verify the issue, location, recipient, and wording, then complete submission in the external service.

Important limits and data use
• AI can miss damage or produce false positives. Review the photo, wording, location, and recipient.
• A boundary match suggests a route; it does not prove road ownership, responsibility, or warranty.
• Karnataka contract suggestions exclude titles whose actual work is only a drain, footpath, sewer, pipeline, light, building, bridge, culvert, or other roadside asset. A retained match is still only probable.
• Pothole AI and Drive require camera and foreground location permission, internet, and your own billed OpenAI API key. Garbage and manhole reporting does not require an OpenAI key.
• Selected resized road-damage images and API requests go to OpenAI. Precise coordinates go to OpenStreetMap Nominatim; structured city/state data gate 31 city routes. Karnataka and exact Hyderabad CURE checks may query official GIS. Downloaded Maharashtra, West Bengal, Punjab, Tamil Nadu, Andhra Pradesh, and Telangana boundaries are checked on-device.
• Routing and highway packs download from this project's GitHub Pages, are SHA-256 verified, and are cached locally. They contain no report, photo, or exact coordinates.
• Choosing an external handoff transfers selected data or connection metadata under that provider's policy.
• Drive recording is optional, visible through Android's foreground-service notification, and off by default.
• The app is not affiliated with or endorsed by any government body.

Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Government-information source directory and exact limits, with direct official links: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.29.0 / version code 47)

```text
Drive can now verify a repaired pothole on a later pass using a strict saved-before/current-after comparison. Only a clear same-place view of completed intact repair becomes Fixed; probable or obscured views do not. Karnataka tender suggestions now exclude drain-, footpath-, sewer-, pipeline-, lighting-, building-, bridge-, culvert-, and other roadside-only contracts, even when their title mentions a road location. Physical Fixed status remains separate from official complaint status.
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
