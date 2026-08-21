# Pothole Reporter

An independent Android app that detects visible road damage and prepares a report for
Greater Mumbai or a supported Karnataka urban local body. It has no project-operated
backend or account system.

<a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>

<sub>Example detected by the app. Select the thumbnail for the full-size photo.</sub>

## What it does

- Captures one photo or samples the road in foreground Drive Mode.
- Uses OpenAI vision to assess pothole cavities, failed patches, surface breakup, and
  ruts or depressions.
- Groups nearby repeat observations into one report; Debug mode keeps each observation.
- Saves reports, photos, and optional recordings locally for review.
- Supports English, Kannada, and Marathi.

### Mumbai

For a location reverse-geocoded to Mumbai City or Mumbai Suburban district, the app
prepares editable evidence and offers four handoff choices: share the evidence, open the
official [Pothole QuickFix](https://play.google.com/store/apps/details?id=com.bmc.potholequickfix)
app (or its Play listing if absent), prefill BMC WhatsApp, or call 1916. A ward shown by
the app is only an OpenStreetMap suggestion and must be checked in the official service.

The app does **not** log in to BMC, bypass OTP, or file a grievance. Opening a channel is
not a submission. A Mumbai report can be marked submitted only after the user records the
official grievance ID returned by BMC.

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
- The project is not affiliated with or endorsed by BMC, the Government of Karnataka, or
  any other government body.
- Selected images and your API key go directly to OpenAI. Exact coordinates go to
  OpenStreetMap Nominatim; Karnataka locations also query Karnataka GIS.
- Choosing WhatsApp, Share, Email, or another external app transfers the selected report
  data to that provider under its own policy. Nothing is submitted automatically.

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
