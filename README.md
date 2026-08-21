# Pothole Reporter

An independent Android app that detects visible road damage and prepares a report for
the Mumbai Metropolitan Region (MMR), Pune Municipal Corporation (PMC), or a supported
Karnataka urban local body. It has no project-operated backend or account system.

<a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>

<sub>Example detected by the app. Select the thumbnail for the full-size photo.</sub>

## What it does

- Captures one photo or samples the road in foreground Drive Mode.
- Uses OpenAI vision to assess pothole cavities, failed patches, surface breakup, and
  ruts or depressions.
- Groups nearby repeat observations into one report; Debug mode keeps each observation.
- Saves reports, photos, and optional recordings locally for review.
- Supports English, Kannada, and Marathi.

### Maharashtra

- **MMR:** targets the [current official MMR extent](https://www.mmrda.maharashtra.gov.in/en/about-us/about-mmr):
  9 municipal corporations, 9 municipal councils, Khalapur Nagar Panchayat, and the
  remaining rural MMR. Valid local polygons select 11 civic bodies; the other eight,
  overlaps, and rural MMR use neutral Aaple Sarkar routing instead of a guessed city.
  The bundled OpenStreetMap outer outline is an approximate routing aid, not the legal
  MMR boundary; it does not yet subtract the Scheduled Areas excluded by the 2019 notice.
- **Pune:** covers only the current Pune Municipal Corporation boundary published by the
  [official PMC GIS](https://iwmsgis.pmc.gov.in/BP_Docs/index.html). It does not include
  Pimpri-Chinchwad Municipal Corporation (PCMC).

The app suggests a civic body from the local boundary and reverse-geocoded place. That
suggestion does **not** establish who owns or maintains the road. The user reviews the
evidence and completes the complaint in the offered official app, portal, WhatsApp,
dialler, or email client. Nothing is submitted automatically. Opening a handoff is recorded
only as opened; an official-channel report can be marked submitted only after the user
records the grievance/reference ID returned by that service.

### Karnataka

The app uses Karnataka GIS and a directory of published municipal contacts to prepare an
editable, addressed email draft. It refuses routing when road class, jurisdiction, or a
published recipient cannot be established.

## Install and use

1. Install `PotholeReporter.apk` from the
   [latest release](https://github.com/coding-parrot/pothole-reporter/releases/latest).
2. Enter your OpenAI API key and allow camera and foreground location access.
3. Capture while safely stopped, or securely mount the phone before starting Drive Mode.
4. Review the image, location, authority, wording, and any probable contract match.
5. Complete the complaint yourself in the external official channel or email app.

Drive Mode must remain in the foreground. Detection, geocoding, and routing need an
internet connection.

## Limits and privacy

- AI can miss damage or produce false positives. No field-validated accuracy percentage
  is claimed.
- The project is not affiliated with or endorsed by any municipal or government body.
- Selected images and your API key go directly to OpenAI. Exact coordinates go to
  OpenStreetMap Nominatim; Karnataka locations also query Karnataka GIS.
- Maharashtra boundary checks use bundled local geometry; coordinates are not sent to
  MMRDA or PMC GIS. Choosing an official app, portal, WhatsApp, Share, Email, or another
  external service transfers the selected data to that provider under its own policy.
- Contract matching is available only for eligible Karnataka routes and is disabled
  throughout Maharashtra.
- Public Nominatim lookups are cached and rate-limited. A large civic deployment should
  use a compliant managed or self-hosted geocoder endpoint.

Read the [privacy policy](https://coding-parrot.github.io/pothole-reporter/privacy.html)
and [data sources and limits](https://coding-parrot.github.io/pothole-reporter/sources.html).

## Build and test

```bash
./tools/build-apk.sh
./tests/run-all.sh
```

See [`docs/SOURCES.md`](docs/SOURCES.md) for provenance and test prerequisites.

## License

MIT. See [LICENSE](LICENSE).
