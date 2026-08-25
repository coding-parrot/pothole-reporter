# Google Play listing copy

Prepared 25 August 2026. Paste only the text inside each code block into Play Console.

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
Pothole Reporter is an independent Android app for documenting potholes, garbage, and open or damaged manholes. It saves editable evidence and complaint drafts on your phone; nothing is filed automatically.

Use Photo while safely stopped. For road damage, securely mount the phone and use foreground Drive while Maps or a call is on screen. AI assesses selected road images. Garbage and manhole reports are user-confirmed and do not use AI. Nearby observations are grouped, and a later live drive can mark a pothole Fixed only after a clear same-place repair comparison.

Coverage
• Mapped operational National Highways and Expressways across India; Rajmargyatra/1033 is checked before regional routing.
• Full-state neutral handoffs in Maharashtra, West Bengal, Punjab, Karnataka, Kerala, Tamil Nadu, Andhra Pradesh, Telangana, Uttar Pradesh, Chhattisgarh, Rajasthan, Goa, Madhya Pradesh, Bihar, and Odisha.
• More-specific reviewed routes remain preferred for MMR, PMC, KMC, supported Karnataka urban bodies and Bengaluru, Greater Chennai, and verified Hyderabad CURE.
• Karnataka uses Janaspandana, with Janahitha for urban issues and helpline 1902. Kerala uses the CMO grievance portal, with K-SMART for local-body issues; 1076 provides help/status but does not accept complaints by phone.
• Uttar Pradesh uses Jansunwai–Samadhan and helpline 1076. Chhattisgarh uses the CM Helpline and helpline 1076, with NIDAAN 1100 only as an urban civic alternate. Rajasthan uses Rajasthan Sampark 2.0 and helpline 181.
• Goa uses CM Helpline Goa/1905; Madhya Pradesh uses CM Helpline/181; Bihar uses Lok Shikayat/1800 345 6284; and Odisha uses Jana Sunani, WhatsApp and 155335.
• All 50 largest Census 2011 population centres, Delhi NCT, and a reviewed Ahmedabad 48-ward footprint. Eight city routes require both a conservative coordinate envelope and exact structured city/state data.

Chandigarh is outside Punjab coverage; Puducherry and Karaikal are outside Tamil Nadu; Yanam is outside Andhra Pradesh; Mahe is outside Kerala; and Delhi NCT is outside Uttar Pradesh and keeps its own route. Locations outside supported routes are saved locally without a recipient.

Every route is a suggestion. You must verify the issue, location, department, local body, road owner, recipient, and wording, then complete the complaint in the external app, portal, WhatsApp, dialler, share sheet, or email client. A boundary does not prove ownership, responsibility, category acceptance, warranty, or submission.

Important limits and data use
• AI can miss damage or produce false positives. Review every result.
• Pothole AI and Drive require camera, foreground location, internet, and your own billed OpenAI API key. Garbage and manhole reporting does not require a key.
• Selected resized road-damage images go to OpenAI. Precise coordinates go to OpenStreetMap Nominatim; Karnataka and exact Hyderabad checks may query official GIS.
• Downloaded state, city, and highway packs are SHA-256 verified and checked on-device. Pack requests contain no report, photo, or exact coordinates.
• Karnataka contract suggestions are optional and probable. Roadside-only drain, footpath, sewer, pipeline, lighting, building, bridge, and culvert work is excluded.
• Drive recording is optional, off by default, and shown through Android's foreground-service notification.
• The app is not affiliated with or endorsed by any government body.

Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Government-information source directory and exact limits, with direct official links: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.34.1 / version code 53)

```text
Reduces speed-breaker false positives in Drive by requiring an explicit speed-breaker check and rejecting raised road features before a pothole is counted.
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
