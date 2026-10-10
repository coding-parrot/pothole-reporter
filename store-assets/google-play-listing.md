# Google Play listing copy

Rewritten 10 October 2026 to match the app as shipped from 1.40 onward (the text live on
Play until then described own-key detection, routes outside Karnataka and background
Drive, none of which this build has). Paste only the text inside each code block into
Play Console.

## App name (16/30 characters)

```text
Pothole Reporter
```

## Short description (70/80 characters)

```text
Detect potholes with your phone camera and prepare an email complaint.
```

## Full description (2986/4000 characters)

```text
Pothole Reporter is an independent Android app that detects road damage with your phone camera, adds it to a shared map and prepares a complaint email for you to send. It is free to use and needs no account and no API key.

How it works
• Photo: while safely stopped, take one photo of the damage.
• Drive: mount the phone with the camera facing the road and start a drive before you move. The app checks the road as you travel while it stays open on screen. If you switch to another app, the drive stops.
• AI assesses each picture for pothole cavities, failed patches, surface breakup, ruts and depressions.
• Each pothole found is saved as a report on your phone with the photo, the street and the time.
• Email complaint: one tap opens an editable draft in your own email app, addressed to the responsible office, with the photo, location and damage details. Nothing is sent for you.
• Pothole map: a public map of India showing road damage reported through the app.

Where complaints can be addressed
Complaint emails are available for city and town roads in Karnataka whose urban local body publishes a contact address, including Bengaluru's city corporations. National, state and district highways, village roads and places outside Karnataka are saved on your phone and shown on the map, but the app does not name a recipient for them yet.

Where a public record matches, a report also lists road works tendered for that street or ward. A tender match is a pointer to check. It is not proof of responsibility or warranty.

Important limits and data use
• AI can miss damage or flag damage that is not there. Review the photo, location, recipient and wording before you send.
• The app needs the camera, precise location and an internet connection.
• By default each checked picture passes through the project service to its detector (OpenAI by default) and is not kept by the service. You can use your own OpenAI key in Settings instead: pictures then go directly to OpenAI and your key never reaches the project service.
• For each pothole found, its exact coordinates, the time, the damage type, an image hash (not the image) and a pseudonymous installation ID go to the project service. The service groups nearby sightings and shows the location on the public map. Names, contact details and photos are not published.
• Coordinates may also go to OpenStreetMap Nominatim to name the street. Viewing the map loads satellite imagery from Esri.
• Reports and photos stay on your phone until you delete them. Faces and number plates in a photo are not blurred.
• Shared detection is paid for by the project and has daily limits. It can be unavailable when a limit is reached.
• The app is not affiliated with or endorsed by any government body.

Source code (MIT licence): https://github.com/coding-parrot/pothole-reporter
Privacy: https://coding-parrot.github.io/pothole-reporter/privacy.html
Data sources and limits: https://coding-parrot.github.io/pothole-reporter/sources.html
```

## Release notes (1.41.1 / version code 85)

```text
The pothole map now opens on your own city, and you can zoom in to a street or out to all of India. The pothole is outlined on the report's photo, and the email attaches the outlined photo and the original.
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

Every sentence of the description must stay true of the build that is live on Play:
change this file in the same commit that changes where complaints can be addressed, what
Drive does when the app is hidden, or what leaves the phone.
