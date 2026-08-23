# Google Play listing copy

Prepared 23 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (74/80 characters)

```text
Detect damage and prepare reports on mapped highways and supported cities.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting visible road damage and preparing an editable civic complaint. Take one photo while safely stopped, or securely mount the phone and use foreground Drive Mode. AI assesses selected images for pothole cavities, failed patches, breakup, ruts, and depressions. Reports stay on your device for review.

Coverage
• Mapped operational National Highways and National Expressways across India. Matches use Rajmargyatra/1033 before municipal routing; the maintaining agency is not guessed.
• Delhi NCT, not the wider NCR. PWD Sewa is offered as a cross-agency handoff; this does not claim PWD owns the road.
• The current Mumbai Metropolitan Region. Valid local polygons can suggest a body; rural, overlapping, and unsupported MMR locations use Aaple Sarkar.
• Pune Municipal Corporation limits. PCMC is excluded.
• Kolkata Municipal Corporation limits. Howrah, Bidhannagar/Salt Lake, New Town, and neighbouring bodies are excluded.
• Supported Karnataka urban local bodies with a published recipient and eligible road classification.
• Greater Chennai Corporation limits, not the wider Chennai Metropolitan Area or neighbouring urban bodies.
• A conservative Hyderabad-core outline only. Coverage is partial, the published Secunderabad Cantonment extent is refused, and My Cure is offered without assigning the point to Greater Hyderabad, Cyberabad, or Malkajgiri Municipal Corporation after the 2026 reorganisation.
• Ahmedabad only when Nominatim returns an exact structured city or municipality match inside a local relevance envelope. This is not an AMC boundary claim.

Locations outside these routes are saved locally but are not addressed to an authority.

Reporting
• Nearby repeat observations are grouped into one draft.
• The app can open a published app, portal, WhatsApp chat, dialler, share sheet, or editable email draft.
• Nothing is submitted automatically. The app does not log in, bypass OTP, press Send, or read complaint status. Complete the complaint in the external service and record its reference ID when required.

Important limits and data use
• AI can miss damage or produce false positives. Review the photo, wording, location, and recipient.
• A boundary or place match suggests a route; it does not prove road ownership.
• Probable contract matching is available only for eligible Karnataka routes and is not proof of responsibility or warranty.
• Core features require camera and foreground location permission, internet, and your own billed OpenAI API key.
• Selected resized images and API requests go directly to OpenAI. Precise coordinates go to OpenStreetMap Nominatim; Karnataka points also query Karnataka GIS.
• Regional routing/contact packs, 2° National Highway tiles, and the optional Karnataka tender pack download from this project's GitHub Pages when relevant. Complete downloads are SHA-256 verified and cached locally. A required routing-data failure stops authority routing; an optional tender-pack failure only omits contract context. Requests identify a state or approximate tile but contain no report, photo, or exact coordinates.
• MMR, PMC, KMC, Delhi NCT, GCC Chennai, and Hyderabad-core polygon checks run locally. Ahmedabad checks a local envelope but intentionally requires Nominatim's exact structured result.
• Choosing an external handoff transfers selected data or connection metadata under that provider's policy.
• Drive recording is optional and off by default. Mount the phone before moving.
• The app is not affiliated with or endorsed by any government body.

Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Government-information source directory and exact limits, with direct official links: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.21.1 / version code 39)

```text
Drive Mode now keeps its live camera view and counters visible while the app is open, and continues under a persistent notification when you switch to Maps or take a call. Stop now finalises the session reliably. Optional silent recording remains off by default.
```

## Play Console fields

- Recommended category: **Tools**.
- Ads declaration: **No**, provided no advertising SDK or ad content is added before release.
- Privacy policy URL:
  `https://coding-parrot.github.io/pothole-reporter/privacy.html`
- Support website: `https://github.com/coding-parrot/pothole-reporter/issues`
- Data-source page:
  `https://coding-parrot.github.io/pothole-reporter/sources.html`
- **Publisher action required:** enter a monitored, developer-controlled support email in
  Play Console. No support email is present in the repository, so none is invented here.

Do not use government marks or describe Pothole Reporter as “official,” a “government
app,” or affiliated with a civic body. Do not claim guaranteed detection, automatic filing,
a verified pothole, road ownership, or a measured accuracy percentage without independent
evidence.
