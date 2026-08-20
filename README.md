# Pothole Reporter

A pure-client Android app that detects road damage, associates the location with a
supported urban local body, and drafts an email complaint. It has no project-operated
backend or account system.

**Current coverage:** supported Karnataka urban local bodies with published contact
emails. National highways and rural roads are excluded.

<a href="docs/example-pothole.jpg"><img src="docs/example-pothole-thumb.jpg" width="280" alt="Pothole detected by Pothole Reporter"></a>

<sub>Example detected by the app. Select the thumbnail for the full-size photo.</sub>

## What it does

- Captures a single photo or samples the road in Drive Mode.
- Uses OpenAI vision to identify pothole cavities, failed patches, surface breakup,
  and ruts or depressions.
- Adds the location, matching municipal boundary, published officer address, and a
  probable road-work contract when a reliable match is available.
- Saves the result as an editable draft and opens the email composer only after you
  choose to send it.
- Records Drive Mode footage on the device by default. It can be disabled in Settings,
  or analysed and deleted later from History.

The app refuses to guess a recipient for national highways, rural roads, locations
outside Karnataka, unknown road ownership, or bodies without a published address.

## Install and use

1. Download `PotholeReporter.apk` from the
   [latest release](https://github.com/coding-parrot/pothole-reporter/releases/latest)
   and sideload it.
2. Enter your OpenAI API key and allow camera and location access.
3. For Drive Mode, mount the phone securely and keep the road inside the orange guide.
   Start the drive before moving and do not interact with the phone while driving.
4. Alternatively, use **Report road damage** while safely stopped.
5. Review the detected damage, location, recipient, contract match, and complaint before
   opening the email draft.

Drive Mode requires the app to remain in the foreground. An internet connection is
required for detection, geocoding, and road classification.

## Important limits

- Detection is not perfect. It can miss damage or produce false positives; review every
  result before sending it.
- Detection defaults to `gpt-5-mini` with high image detail. The `gpt-5.6`/original-detail
  option is experimental; neither has a complete held-out, human-labelled v3 field
  benchmark. See [`eval/README.md`](eval/README.md).
- Contract matches are probable matches, not proof of responsibility or warranty.
- The app does not send email automatically and is not affiliated with any government
  body.

## Cost and privacy

- API usage is billed to your OpenAI key. Drive Mode can make many image requests, so a
  long drive costs more than a single-photo report.
- Drive recording uses roughly 18 MB per minute while enabled. Successful reanalysis
  deletes it unless Debug mode is keeping it.
- Checked images are sent to OpenAI. Faces, number plates, and shopfronts are not blurred.
- Your API key is stored locally and sent to OpenAI as the request credential.
- Coordinates are sent to OpenStreetMap Nominatim for addresses and Karnataka GIS for
  boundary and road classification.
- Reports, labels, and footage are stored locally. Complaint content and attachments are
  handed to your email app when you open a draft. The project operates no collection
  server.

## Development

The maintained source files are:

- `static/index.html` — interface and capture workflow
- `static/standalone.js` — detection, storage, routing, and drafting engine

Build the Android APK with:

```bash
./tools/build-apk.sh
```

The script mirrors the static files into the Android project, builds the APK, verifies
the packaged assets, and scans the packaged engine for an OpenAI-style key.

Run the complete test suite with:

```bash
./tests/run-all.sh
```

These commands assume the Node, Android/JDK, Python, Playwright, and Gradle dependencies
are already installed. The suite requires `OPENAI_API_KEY` in `.env`; some tests call
live OpenAI and Karnataka GIS services. Data-source notes are in
[`docs/SOURCES.md`](docs/SOURCES.md).

## License

MIT. See [LICENSE](LICENSE).
