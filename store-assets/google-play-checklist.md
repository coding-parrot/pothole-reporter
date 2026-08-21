# Google Play publication checklist

Status reviewed 21 August 2026 for release 1.14.0 / version code 29. This is a release
checklist, not a substitute for the current Play Console tasks shown for the publisher's account.

## Go/no-go blockers

- [x] **Target API:** the Android project targets API 36, meeting the mobile-app rule that
  starts 31 August 2026. Recheck before every later update.
- [ ] **Fresh signed release bundle:** build version 1.14.0/code 29 after the Mumbai changes,
  confirm it is non-debuggable and signed with the upload key kept outside Git, then inspect
  it in Play Console. The previous 1.13.0/code 28 bundle does not contain this launch.
- [ ] **Hosted privacy page:** enable a stable public host, then verify
  `https://coding-parrot.github.io/pothole-reporter/privacy.html` in a signed-out
  browser. It must not redirect to a login, return an error, or serve a PDF.
- [x] **In-app privacy link:** link that exact hosted page from the release build. Creating
  the page alone does not satisfy the in-app-link requirement.
- [ ] **Support email:** supply a monitored, developer-controlled email address in Play
  Console. The repository contains none, so this remains a publisher decision.
- [ ] **Reviewer access:** core detection requires an OpenAI key. Give Play reviewers
  reusable English instructions and a dedicated revocable, spend-limited credential that
  unlocks every reviewable feature. Do not expose a personal production key.
- [ ] **Data Safety and App content:** complete and submit the declarations below; do not
  claim that the app collects no data.
- [ ] **Government-information declaration:** disclose that the app is independent,
  identify its official government sources, and complete Play's Government apps
  declaration without claiming BMC affiliation.
- [ ] **Account gates:** complete any identity, device-verification, package-registration,
  or closed-testing task Play Console shows for this publisher account.

Do not submit to production until every applicable item above is complete.

## Release artifact and testing

- [ ] Use the fixed package name `com.gauravsen.potholereporter` and a version code not
  previously uploaded to Play.
- [ ] Generate a signed **release AAB**, inspect it in Play's App Bundle Explorer, and save
  the upload key in a backed-up secret store outside the repository.
- [ ] Install the Play-generated build from the internal track on at least one supported
  physical device. Test first launch, disclosure/permissions, manual capture, Drive Mode,
  stopping/final clip, footage analysis/deletion, history, wipe, API-key errors, offline
  errors, and Karnataka email-composer hand-off.
- [ ] Test a Greater Mumbai report in English and Marathi. Verify evidence sharing,
  the installed Pothole QuickFix app or its Play listing, prefilled WhatsApp to +91 89992 28999, the 1916 dialler,
  installed/uninstalled external-app behavior, and cancellation/back navigation.
- [ ] Verify that Pothole QuickFix changes only to “handoff opened,” while Share, WhatsApp,
  and Call never mark the report submitted. Confirm that Mark submitted rejects a
  missing/invalid grievance ID before storing the official ID locally.
- [ ] Confirm the release contains no API key, test frame, private location, or debug-only
  setting and that its bundled web assets match reviewed source.
- [ ] Review the Pre-launch report and address crashes, ANRs, accessibility failures, and
  policy warnings before widening the track.

Official references: [target API schedule](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en),
[Android App Bundles](https://developer.android.com/guide/app-bundle), and
[app signing](https://developer.android.com/studio/publish/app-signing).

## Store listing

- [ ] Paste the reviewed title, short description, full description, and release notes from
  [`google-play-listing.md`](google-play-listing.md).
- [ ] Replace or re-verify the icon, feature graphic, and four phone screenshots against
  the 1.14.0 release. Include the independent Mumbai handoff and Marathi without displaying
  private coordinates, an API key, a real grievance ID, or BMC marks.
- [ ] Use screenshots from the release build and avoid implying government affiliation,
  automatic filing, guaranteed detection, verified responsibility, or benchmarked accuracy.
- [ ] Enter the hosted privacy-policy URL, support website, and required support email.
- [ ] Choose the app category and target countries intentionally. Current routing is limited
  to Greater Mumbai and supported Karnataka urban local bodies; keep that limit visible.
- [ ] Keep the non-affiliation statement and official-source URLs visible in the full
  description. Do not use BMC's name, seal, logo, colours, or screenshots in a way that
  suggests this is BMC's app.

## Privacy and Data Safety

- [ ] Make the privacy policy, in-app disclosure, Play Data Safety answers, and actual
  release behavior agree exactly.
- [ ] Treat off-device transmission as collection even when processing is short-lived.
  Audit at least:
  - selected manual photos and Drive/VOD image frames sent to OpenAI;
  - precise coordinates sent to Nominatim, and to Karnataka GIS only for Karnataka points;
  - road address and procurement shortlist sent to OpenAI for probable contract matching;
  - API credential and standard network metadata received by external services;
  - map-area tile requests, email-app hand-off, Android sharing, and every included SDK;
  - Mumbai report text passed in the WhatsApp link, the Pothole QuickFix app/Play-listing request,
    compressed evidence passed to the chosen share destination, and the 1916 dialler action;
  - the official BMC grievance ID stored locally when the user marks a report submitted.
- [ ] For each applicable Play data type, answer collection/sharing, purpose,
  required-versus-optional, ephemeral processing, retention/deletion, and encryption in
  transit. Determine “shared” only after documenting whether each recipient meets Google's
  service-provider or user-initiated-transfer exceptions.
- [ ] Do not mark **No data collected**: images and precise coordinates leave the device.
- [ ] Verify that disclosure and affirmative consent appear before camera/location access
  and explain named recipients, off-device processing, unblurred imagery, and local video
  retention in clear language.
- [x] Shared evidence and exported datasets use a dedicated app-cache folder; the in-app
  wipe deletes that folder. Copies already handed to another app remain under that
  destination's control, as the privacy policy explains.
- [x] Verify the remaining local deletion claims: the in-app wipe clears reports, photos,
  drive summaries, app-held footage, key/name/settings, and the app's Documents debug-frame
  directory. Files copied, shared, attached, or sent elsewhere remain under that destination's control.
- [ ] Re-audit all answers whenever an SDK, model provider, endpoint, permission, storage
  rule, or retention behavior changes.

Official references: [User Data and privacy policy](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en),
[Data Safety](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en),
and Google's [July 2026 AI/location clarification](https://support.google.com/googleplay/android-developer/answer/17134731?hl=en).

## App content and review access

- [ ] Complete the IARC content-rating questionnaire accurately and repeat it after any
  relevant content or feature change.
- [ ] Declare whether the release contains ads. The reviewed code has no ad SDK; select
  **No** only if that remains true in the uploaded bundle.
- [ ] Select only target age groups the product is genuinely designed for. Do not include
  children unless the app, data practices, SDKs, and listing are ready for Families policy.
- [ ] In **App access**, explain first-run disclosure, permissions, entering the supplied
  review API key, manual capture, Drive Mode, History, Karnataka email, and each Mumbai
  handoff. Access details must remain valid, reusable, and available throughout review.
- [ ] If Mumbai or Karnataka routing is hard for an overseas reviewer to exercise, include
  lawful, repeatable review steps and test material that expose both flows without falsifying
  device location or policy declarations.
- [ ] Complete the Government apps declaration as an unaffiliated app that communicates
  government information. Keep easy-to-see official source URLs and the explicit statement
  that the app neither represents BMC nor submits a grievance automatically.
- [ ] Do not add BMC marks, copied BMC graphics, embedded BMC pages, or deep links to BMC's
  website without written permission. BMC's published website policy restricts linking,
  framing, caching, and reuse of graphics; the current handoff uses Google Play, WhatsApp,
  and the dialler instead.

Official references: [content ratings](https://support.google.com/googleplay/android-developer/answer/9898843?hl=en),
[prepare app for review](https://support.google.com/googleplay/android-developer/answer/9859455?hl=en),
[review sign-in/access details](https://support.google.com/googleplay/android-developer/answer/15748846?hl=en),
[Government apps](https://support.google.com/googleplay/android-developer/answer/9514050?hl=en),
and [Impersonation](https://support.google.com/googleplay/android-developer/answer/9888374?hl=en).

## Publisher account and rollout

- [ ] Verify legal identity and contact details. A personal account may require government
  ID; an organization account normally requires D-U-N-S, identity, and organization
  documents. Use the account type that truthfully represents the publisher.
- [ ] For a new personal account, complete the physical-device task in the Play Console
  mobile app on a non-rooted Android 10+ device if Play shows it.
- [ ] If the personal account was created after 13 November 2023, run a closed test with at
  least 12 testers continuously opted in for at least 14 days, then apply for production
  access. Internal testing does not replace this gate.
- [ ] After identity verification, check Play Console's Android developer-verification page
  and confirm the package is registered. All Play packages must be registered by
  30 September 2026; manually register if auto-registration did not succeed.
- [ ] Keep evidence of tester feedback, fixes made, and production readiness for the access
  application. Allow calendar time beyond the minimum 14-day test.
- [ ] Start with internal, then closed, then a cautious staged production rollout. Monitor
  crashes, ANRs, reviews, OpenAI cost/rate failures, and policy messages; keep a rollback
  decision ready.

Official references: [personal-account testing gate](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en),
[device verification](https://support.google.com/googleplay/android-developer/answer/14316361?hl=en),
[identity verification](https://support.google.com/googleplay/android-developer/answer/10841920?hl=en),
and [package registration](https://support.google.com/googleplay/android-developer/answer/16984799?hl=en).
