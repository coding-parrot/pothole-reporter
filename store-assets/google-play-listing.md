# Google Play listing copy

Prepared 21 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (72/80 characters)

```text
Detect road damage and prepare MMR, PMC or Karnataka reports for review.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting visible road damage and preparing a report for the Mumbai Metropolitan Region (MMR), Pune Municipal Corporation (PMC), or a supported Karnataka urban local body.

Take one photo while safely stopped, or securely mount the phone and use Drive Mode while the app remains in the foreground. AI assesses selected images for pothole cavities, failed patches, surface breakup, and ruts or depressions. Reports and optional recordings are kept on your device for review.

Maharashtra
• Cover the current official MMR extent: 9 municipal corporations, 9 municipal councils, Khalapur Nagar Panchayat, and rural MMR. Local polygons select 11 civic bodies; other, rural, or overlapping locations use neutral Aaple Sarkar routing instead of a guessed city.
• Cover current Pune Municipal Corporation limits using PMC's published GIS boundary. PCMC is not included.
• Prepare editable English or Marathi evidence and offer the published app, portal, email, WhatsApp, share, or call action recorded for the suggested civic body.
• Record the official grievance/reference ID returned by an app or portal before marking that local report submitted.

The civic body is a suggestion, not proof of who owns or maintains the road. The app does not log in, bypass OTP, submit a grievance, press Send, or read complaint status. Opening a channel means only that the handoff opened. You must review the evidence and complete submission yourself in the external official service. Contract matching is disabled throughout Maharashtra.

Karnataka
For supported urban local bodies, public map data and a bundled directory help identify a published municipal recipient. The app opens an editable email draft and never presses Send. When available, it may add a clearly labelled probable match to a public procurement record.

Important
• AI can miss road damage or produce false positives. Review every result.
• A probable contract match is not proof of responsibility or warranty.
• The app is not affiliated with or endorsed by any municipal or government body.
• Camera and foreground location permission, internet access, and your own OpenAI API key are required for core features. OpenAI usage is billed to your account.
• Selected road images and the API request are sent to OpenAI. Precise coordinates are sent to OpenStreetMap Nominatim; Karnataka locations also query Karnataka GIS. MMR and PMC boundary checks use geometry bundled in the app.
• Choosing an official app, portal, WhatsApp, Share, Email, or another external service transfers selected data or connection metadata to that provider under its own privacy policy.
• Drive recording is optional and off by default. Mount the phone before moving and never interact with it while driving.

Government information sources
BMC portal: https://www.mcgm.gov.in
Official Pothole QuickFix listing: https://play.google.com/store/apps/details?id=com.bmc.potholequickfix
MMRDA official MMR scope: https://www.mmrda.maharashtra.gov.in/en/about-us/about-mmr
PMC official GIS: https://iwmsgis.pmc.gov.in/BP_Docs/index.html
Aaple Sarkar grievances: https://grievances.maharashtra.gov.in/en
Karnataka GIS: https://kgis.ksrsac.in/kgismaps/rest/services
Karnataka Public Procurement Portal: https://kppp.karnataka.gov.in

Privacy and detailed source limits:
https://coding-parrot.github.io/pothole-reporter/privacy.html
https://coding-parrot.github.io/pothole-reporter/sources.html

Current routing targets the MMR, Pune Municipal Corporation limits, and supported Karnataka urban local bodies. The MMR outer outline is an approximate routing aid; PCMC and other locations outside supported routes are saved locally but not addressed to an authority.
```

## Release notes (1.15.0 / version code 30)

```text
Expanded Maharashtra routing across the MMR roster and outer region, plus current Pune Municipal Corporation limits. Added polygon-verified civic handoffs, safe Aaple Sarkar fallback where no civic polygon exists, and official-reference tracking. The MMR outer outline is approximate, PCMC is excluded, authority suggestions do not prove road ownership, and complaints are never filed automatically.
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

Do not use government marks or describe Pothole Reporter as “official,” a “government
app,” or affiliated with a civic body. Do not claim guaranteed detection, automatic filing,
a verified pothole, road ownership, or a measured accuracy percentage without independent
evidence.
