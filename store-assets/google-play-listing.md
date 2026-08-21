# Google Play listing copy

Prepared 21 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (70/80 characters)

```text
Detect road damage and prepare Mumbai or Karnataka reports for review.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting visible road damage and preparing a report for Greater Mumbai or a supported Karnataka urban local body.

Take one photo while safely stopped, or securely mount the phone and use Drive Mode while the app remains in the foreground. AI assesses selected images for pothole cavities, failed patches, surface breakup, and ruts or depressions. Reports and optional recordings are kept on your device for review.

Greater Mumbai
• Prepare editable report text in English or Marathi.
• Share compressed image evidence through Android's share sheet.
• Open the installed official Pothole QuickFix app (or its Play listing), prefill the published BMC WhatsApp channel, or call 1916.
• Record the official grievance ID returned by BMC before marking the local report submitted.

The app does not log in to BMC, bypass OTP, submit a grievance, or read its status. Opening a channel is only a handoff. You must review the evidence and complete submission yourself in the external official service. Any ward shown is an OpenStreetMap suggestion, not an official boundary or road-ownership decision.

Karnataka
For supported urban local bodies, public map data and a bundled directory help identify a published municipal recipient. The app opens an editable email draft and never presses Send. When available, it may add a clearly labelled probable match to a public procurement record.

Important
• AI can miss road damage or produce false positives. Review every result.
• A probable contract match is not proof of responsibility or warranty.
• The app is not affiliated with or endorsed by Brihanmumbai Municipal Corporation, the Government of Karnataka, or any other government body.
• Camera and foreground location permission, internet access, and your own OpenAI API key are required for core features. OpenAI usage is billed to your account.
• Selected road images and the API request are sent to OpenAI. Precise coordinates are sent to OpenStreetMap Nominatim; Karnataka locations also query Karnataka GIS.
• Choosing WhatsApp, Share, Email, or another external app transfers the selected data to that provider under its own privacy policy.
• Drive recording is optional and off by default. Mount the phone before moving and never interact with it while driving.

Government information sources
BMC portal: https://www.mcgm.gov.in
Official Pothole QuickFix listing: https://play.google.com/store/apps/details?id=com.bmc.potholequickfix
Karnataka GIS: https://kgis.ksrsac.in/kgismaps/rest/services
Karnataka Public Procurement Portal: https://kppp.karnataka.gov.in

Privacy and detailed source limits:
https://coding-parrot.github.io/pothole-reporter/privacy.html
https://coding-parrot.github.io/pothole-reporter/sources.html

Current coverage is Greater Mumbai and supported Karnataka urban local bodies. Locations outside those routes are saved locally but not addressed to an authority.
```

## Release notes (1.14.0 / version code 29)

```text
Added Greater Mumbai report preparation, Marathi, compressed evidence sharing, and handoffs to Pothole QuickFix, BMC WhatsApp, and 1916. Mumbai reports require the official BMC grievance ID before they can be marked submitted. Complaints are never filed automatically.
```

## Play Console fields

- Recommended category: **Tools**.
- Ads declaration: **No**, provided no advertising SDK or ad content is added before release.
- Privacy policy URL after GitHub Pages is enabled and verified:
  `https://coding-parrot.github.io/pothole-reporter/privacy.html`
- Support website: `https://github.com/coding-parrot/pothole-reporter/issues`
- Data-source page after GitHub Pages is enabled and verified:
  `https://coding-parrot.github.io/pothole-reporter/sources.html`
- **Publisher action required:** enter a monitored, developer-controlled support email in
  Play Console. No support email is present in the repository, so none is invented here.

Do not use BMC marks or describe Pothole Reporter as “official,” a “government app,” or
affiliated with BMC. Do not claim guaranteed detection, automatic filing, a verified
pothole, or a measured accuracy percentage without independent evidence.
