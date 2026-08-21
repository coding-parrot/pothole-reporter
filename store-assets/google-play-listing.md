# Google Play listing copy

Prepared 21 August 2026. Paste only the text inside each code block into Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (78/80 characters)

```text
Detect road damage and prepare Delhi, MMR, Pune, Kolkata or Karnataka reports.
```

## Full description

```text
Pothole Reporter is an independent Android app for documenting visible road damage and preparing a report for Delhi NCT, the Mumbai Metropolitan Region (MMR), Pune Municipal Corporation (PMC), Kolkata Municipal Corporation (KMC), or a supported Karnataka urban local body.

Take one photo while safely stopped, or securely mount the phone and use foreground Drive Mode. AI assesses selected images for pothole cavities, failed patches, surface breakup, and ruts or depressions. Reports stay on your device for review.

Maharashtra
• Cover the current MMR extent. Valid local polygons select a civic body; rural, overlapping, or unsupported MMR locations use Aaple Sarkar instead of a guessed city.
• Cover current PMC limits using PMC's published GIS boundary. PCMC is excluded.
• Prepare editable English or Marathi evidence and offer the published external handoff.

Kolkata
• Cover current KMC limits using the official West Bengal UDMA municipal boundary bundled in the app.
• Exclude Howrah, Bidhannagar/Salt Lake, New Town, and other neighbouring civic bodies.
• Prepare English or Bengali evidence and offer KMC Grievance 2.0, the official KMC app as an alternate, KMC WhatsApp, or the KMC helpline.

Delhi
• Cover the full National Capital Territory of Delhi and exclude the wider NCR, including Noida, Gurugram, Ghaziabad and Faridabad.
• Offer PWD Sewa as a cross-agency road-grievance handoff, with Delhi PGMS as an alternate, plus published WhatsApp and 1908 channels.
• Use the local NCT boundary only for coverage. It does not identify who owns or maintains a road.

Karnataka
For supported urban local bodies, public GIS and a bundled directory help identify a published municipal recipient. The app opens an editable email draft. An eligible report may include a clearly labelled probable public-procurement match.

Important
• AI can miss damage or produce false positives. Review every result.
• A civic boundary suggests a body; it does not prove road ownership.
• Nothing is submitted automatically. The app does not log in, bypass OTP, press Send, or read complaint status. Complete the complaint in the external service. Where required, enter its grievance/reference ID before marking the local report submitted.
• Contract matching is Karnataka-only and is not proof of responsibility or warranty.
• The app is not affiliated with or endorsed by any government body.
• Core features require camera and foreground location permission, internet, and your own billed OpenAI API key.
• Selected images and API requests go to OpenAI. Precise coordinates go to OpenStreetMap Nominatim; Karnataka points also query Karnataka GIS. MMR, PMC, KMC, and Delhi NCT boundary checks are local.
• Choosing an official app, portal, WhatsApp, Share, Email, or another external service transfers selected data or connection metadata under that provider's policy.
• Drive recording is optional and off by default. Mount the phone before moving.

Government information sources
MMRDA: https://www.mmrda.maharashtra.gov.in/en/about-us/about-mmr
PMC GIS: https://iwmsgis.pmc.gov.in/BP_Docs/index.html
West Bengal UDMA Nagar GIS: https://nagargispariseva.wb.gov.in
KMC Grievance 2.0: https://kmc.wb.gov.in/citizen/language-selection
Official KMC app: https://play.google.com/store/apps/details?id=com.kmc.app
Delhi PWD Sewa: https://www.pwddelhi.gov.in/sewa/complaint
Delhi PGMS: https://pgms.delhi.gov.in/
Karnataka GIS: https://kgis.ksrsac.in/kgismaps/rest/services
Karnataka procurement: https://kppp.karnataka.gov.in

Privacy and source limits:
https://coding-parrot.github.io/pothole-reporter/privacy.html
https://coding-parrot.github.io/pothole-reporter/sources.html

Current routing targets Delhi NCT, the MMR, PMC, KMC, and supported Karnataka urban local bodies. Locations outside these routes are saved locally but not addressed to an authority.
```

## Release notes (1.17.0 / version code 33)

```text
Added full Delhi NCT coverage with PWD Sewa, Delhi PGMS, WhatsApp and 1908 handoffs. The wider NCR is excluded, and the app does not guess who owns a road.
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
