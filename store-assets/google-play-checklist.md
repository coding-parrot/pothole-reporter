# Google Play publication checklist

Status reviewed 24 August 2026 for release 1.29.0 / version code 47. This is a release
checklist, not a substitute for the current Play Console tasks shown for the publisher's account.

## Go/no-go blockers

- [x] **Target API:** the Android project targets API 36, meeting the mobile-app rule that
  starts 31 August 2026. Recheck before every later update.
- [ ] **Fresh signed release bundle:** build version 1.29.0/code 47 after the repair-status and tender-scope changes,
  confirm it is non-debuggable and signed with the upload key kept outside Git, then inspect
  it in Play Console.
- [x] **Hosted privacy page:** verified on the stable public host:
  `https://coding-parrot.github.io/pothole-reporter/privacy.html` in a signed-out
  browser. It must not redirect to a login, return an error, or serve a PDF.
- [x] **In-app privacy link:** link that exact hosted page from the release build. Creating
  the page alone does not satisfy the in-app-link requirement.
- [ ] **Support email:** enter and monitor `contact@aiengg.dev` in Play Console.
- [ ] **Reviewer access:** Pothole and Drive require an OpenAI key; Garbage and Manhole do
  not. Give Play reviewers
  reusable English instructions and a dedicated revocable, spend-limited credential that
  unlocks every reviewable feature. Do not expose a personal production key.
- [ ] **Data Safety and App content:** complete and submit the declarations below; do not
  claim that the app collects no data.
- [ ] **Government-information declaration:** disclose that the app is independent,
  identify its official government sources, and complete Play's Government apps
  declaration without claiming affiliation with any named civic body or complaint service.
- [ ] **Account gates:** complete any identity, device-verification, package-registration,
  or closed-testing task Play Console shows for this publisher account.

Do not submit to production until every applicable item above is complete.

## Release artifact and testing

- [ ] Use the fixed package name `dev.aiengg.potholereporter` and a version code not
  previously uploaded to Play.
- [ ] Generate a signed **release AAB**, inspect it in Play's App Bundle Explorer, and save
  the upload key in a backed-up secret store outside the repository.
- [ ] Install the Play-generated build from the internal track on at least one supported
  physical device. Test first launch, disclosure/permissions, manual capture, Drive Mode,
  stopping/final clip, footage analysis/deletion, history, wipe, API-key errors, offline
  errors, and Karnataka email-composer hand-off.
- [ ] On a clean install, first test every region online so each checksum-pinned routing pack
  can download. Then repeat a cached route offline, corrupt a test pack, and confirm the app
  fails closed rather than routing from unverified data.
- [ ] Test mapped NH/NE samples in west, north, south, and inside Delhi; a junction with two
  references; poor GPS; conflicting drive heading; an absent tile; and a corrupt tile. Confirm
  highways route before cities to Rajmargyatra/1033, no maintaining agency is claimed, and
  unsafe or unavailable highway data fails closed.
- [ ] Test representative locations in all 19 MMR urban local bodies, a rural MMR point,
  Pune inside PMC, PCMC, Nagpur, Nashik, Kolhapur, Solapur, a rural Maharashtra point, and
  neighbouring-state fixtures. Confirm rural/ambiguous MMR uses Aaple Sarkar, PMC uses Road
  Mitra/PMC CARE, other Maharashtra points use the statewide handoff, and outside points fail closed.
- [ ] Test central, north, south, and Joka locations inside KMC; Howrah,
  Bidhannagar/Salt Lake, New Town, Siliguri, Durgapur, Asansol, district/rural points, a
  neighbouring-state point, and a state-edge accuracy case. Confirm only the checksum-verified
  KMC polygon selects KMC, every other confidently contained West Bengal point uses PGRS,
  and a place name alone never selects either route.
- [ ] Test Punjab fixtures in Amritsar, Ludhiana, rural districts, the state edge,
  Chandigarh, Panchkula, and Ambala. Confirm only the checksum-verified Punjab polygon
  offers Connect Punjab, Chandigarh stays outside, and a place name alone never routes.
- [ ] Test Tamil Nadu fixtures in Chennai, Coimbatore, Madurai, Tiruchirappalli, Salem,
  a rural district, the state edge, Puducherry, and Karaikal. Confirm National Highways run
  first, the exact GCC route remains preferred, only the checksum-verified state polygon
  offers Mudhalvarin Mugavari elsewhere, the complete GPS-accuracy circle must be inside,
  and Puducherry/Karaikal stay excluded. Confirm the statewide route offers only
  Mudhalvarin Mugavari and does not guess a secondary municipal portal.
- [ ] Test Andhra Pradesh fixtures in Visakhapatnam, Vijayawada, Guntur, Tirupati, Kurnool,
  a rural district, the state edge, Yanam, and neighbouring states. Confirm National
  Highways run first; only the checksum-verified state polygon offers PGRS, Puramithra as
  an urban alternate, and helpline 1902; the complete GPS-accuracy circle must be inside;
  and Yanam stays excluded. Repeat Pothole, Garbage, and Manhole, and confirm no local body,
  road owner, category acceptance, contract, complaint-write API, or submission is inferred.
- [ ] Test Telangana fixtures in Hyderabad, Warangal, Nizamabad, Adilabad, Khammam,
  Mahabubnagar, Bhadrachalam, rural districts, the state edge, and neighbouring states.
  Confirm National Highways run first; only the checksum-pinned state polygon offers
  neutral Prajavani; the complete GPS-accuracy circle must be inside; Citizen Buddy is
  described only as an urban alternate outside Hyderabad; and no local body, road owner,
  category acceptance, contract, complaint-write API, or submission is inferred.
- [ ] Test all 31 additional top-50 city centres for Pothole, Garbage, and Manhole, plus
  one outside-envelope, wrong-state, stale-city, missing-geocode, and boundary-touching
  fixture per state group. Confirm the coordinate envelope and exact structured
  city/municipality plus state fields are all required, National Highways remain first,
  and no route claims complete Urban Agglomeration coverage or a verified owner.
- [ ] Test central, north, south, east, and west Delhi NCT locations, plus Noida,
  Gurugram, Ghaziabad, Faridabad, and a boundary-edge accuracy case. Confirm only the
  pinned NCT polygon selects the Delhi recipient; Noida and Gurugram stay unrouted, while
  Ghaziabad and Faridabad can route only through their independent top-50 match.
- [ ] Test Chennai fixtures in every GCC zone, St Thomas Mount, and neighbouring bodies.
  Confirm the verified GCC polygon routes only GCC points and preserves its interior hole;
  non-GCC points confidently inside Tamil Nadu use the statewide route rather than GCC.
- [ ] Test representative points across the official 2,053 km² Hyderabad CURE, points just
  outside it, a failed TGRAC response, and Secunderabad Cantonment. Confirm the full
  GPS-accuracy envelope must be inside CURE and avoid Cantonment before selecting My Cure;
  the web/PWA build, failed service, and Cantonment intersection cannot select My Cure but
  can use neutral Prajavani after exact state containment; and neither route claims one of
  the three 2026 corporations.
- [ ] Test central, edge, and just-outside points against the reviewed 48-ward Ahmedabad
  union, including South Bopal/Ghuma as known outer-expansion gap fixtures. Confirm the
  app does not claim current outer AMC or wider AUDA completeness and never falls back to
  an Ahmedabad place-name guess.
- [ ] Across Maharashtra, West Bengal, Punjab, Tamil Nadu, Andhra Pradesh, Telangana, the accepted top-50 routes, Delhi, all five
  Bengaluru city corporations, Chennai, Hyderabad, and Ahmedabad, create Pothole, Garbage,
  and Manhole reports. Confirm road damage keeps
  its road-specific route, the other categories use only reviewed general-civic channels,
  and unverified categories fail closed. Simulate a routing-pack/network failure, keep the
  full civic photo and original timestamp, then confirm Retry routing uses the saved point.
- [ ] Test Maharashtra handoffs in English and Marathi, including email, portal, installed
  and uninstalled official apps, Share, BMC WhatsApp at +91 89992 28999, 1916, cancellation,
  and back navigation.
- [ ] Test Kolkata handoffs in English and Bengali: KMC Grievance 2.0, installed and
  uninstalled official KMC app, WhatsApp at +91 83359 88888, helpline 1800 345 3375,
  cancellation, and back navigation.
- [ ] Test non-KMC West Bengal handoffs in English and Bengali: West Bengal PGRS, the CMO
  Grievance alternate, cancellation, back navigation, and the instruction to select and
  verify the district or department. Confirm no local body, road owner, contract, category
  acceptance, or submission is claimed.
- [ ] Test Delhi handoffs: installed and uninstalled PWD Sewa app, complaint portal,
  Delhi PGMS alternate, WhatsApp at +91 81301 88222, helpline 1908, cancellation, and
  back navigation.
- [ ] Test Chennai handoffs: installed and uninstalled Namma Chennai package
  `com.ceedeev.grivenancev2`, GCC portal fallback and alternate, WhatsApp at
  +91 94450 61913, helpline 1913, cancellation, and back navigation.
- [ ] Test statewide Tamil Nadu handoffs: installed and uninstalled Mudhalvarin Mugavari
  package `org.tnega.cmhelpline.citizen`, portal fallback, and helpline 1100. Confirm no
  owner, category, contractor, API submission, or successful filing is claimed.
- [ ] Test statewide Andhra Pradesh handoffs: PGRS primary, Puramithra urban alternate,
  and helpline 1902, including cancellation and back navigation. Confirm all three issue
  types remain user-completed and no department, local body, road owner, category,
  contractor, complaint-write API, automatic submission, or successful filing is claimed.
- [ ] Test statewide Telangana handoffs: Prajavani primary and Citizen Buddy urban alternate
  outside Hyderabad, including cancellation and back navigation. Confirm all three issue
  types remain user-completed and no department, local body, road owner, category,
  contractor, complaint-write API, automatic submission, or successful filing is claimed.
- [ ] Test Hyderabad handoffs: installed and uninstalled My Cure package `cgg.gov.ghmc`,
  OTP-bound web fallback, complaint-status alternate, cancellation, and back navigation.
  Confirm a failed CURE check never opens My Cure and instead offers only neutral Prajavani
  after exact state containment.
- [ ] Test Ahmedabad handoffs: installed and uninstalled AMC CCRS package
  `com.amplvb.ccrs`, portal fallback, channel-instructions alternate, WhatsApp at
  +91 75678 55303, helpline 155303, cancellation, and back navigation.
- [ ] Verify that opening an app or portal changes only to “official handoff opened,” while
  Share, WhatsApp, Email, and Call never mark the report submitted. Confirm that Mark
  submitted rejects a missing/invalid grievance/reference ID when one is required.
- [ ] Create an open pothole, revisit it on a different live drive, and verify that a
  generic clean-road result does not close it. Confirm only clear same-footprint completed
  repair becomes Fixed; probable repair stays in review; weak GPS, opposite heading,
  ambiguity, missing old evidence, VOD, and Debug all fail closed. Confirm Fixed never
  overwrites or implies an official grievance status, and new damage creates a recurrence.
- [ ] Verify every regional report states that the route is only a suggestion and does not
  claim road ownership. Outside eligible Karnataka coverage, confirm no contract or warranty
  match appears; eligible Karnataka matches must remain explicitly probable and reviewable.
- [ ] Confirm `BBMP/2023-24/OW/WORK_INDENT2505` and other drain-, footpath-, UGD-,
  pipeline-, lighting-, building-, bridge-, and culvert-only rows never enter a shortlist,
  while explicit mixed road-and-drain work remains eligible.
- [ ] Confirm the release contains no API key, test frame, private location, debug-only
  setting, or legacy regional dataset; verify bundled web assets and all 13 resources in the
  v1.29 state-pack manifest against the reviewed byte count and SHA-256. Confirm the old
  unversioned, v1.26, v1.27, and v1.28 manifests remain byte-for-byte immutable.
- [ ] Review the Pre-launch report and address crashes, ANRs, accessibility failures, and
  policy warnings before widening the track.

Official references: [target API schedule](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en),
[Android App Bundles](https://developer.android.com/guide/app-bundle), and
[app signing](https://developer.android.com/studio/publish/app-signing).

## Store listing

- [ ] Paste the reviewed title, short description, full description, and release notes from
  [`google-play-listing.md`](google-play-listing.md).
- [ ] Replace or re-verify the icon, feature graphic, and four phone screenshots against
  the 1.29.0 release. Include the issue picker, an independent civic handoff, and regional-language support
  without displaying private coordinates, an API key, a real grievance ID, or civic-body
  marks. Put other city-specific flows in reviewer instructions instead of implying that
  one screenshot proves every supported route.
- [ ] Use screenshots from the release build and avoid implying government affiliation,
  automatic filing, guaranteed detection, verified responsibility, or benchmarked accuracy.
- [ ] Enter the hosted privacy-policy URL, support website, and required support email.
- [ ] Choose the app category and target countries intentionally. Current routing covers mapped
  NH/NE carriageways across India plus the areas listed in `google-play-listing.md`; keep the Delhi-NCT versus separate NCR-city distinction, wider
  exact GCC versus statewide Tamil Nadu routing, Puducherry/Karaikal exclusions, statewide
  Andhra Pradesh routing and the Yanam exclusion, statewide Telangana routing, exact My Cure
  precedence and the neutral Prajavani fallback for Cantonment,
  and Ahmedabad outer-expansion limitations visible; explain that
  non-KMC West Bengal routes require the user to select the responsible district or department.
- [ ] Keep the non-affiliation statement and the clearly labelled government-information
  source directory visible in the full description; confirm that directory exposes direct
  official links. Do not use civic names, seals, logos, colours, or screenshots in a way
  that suggests affiliation with a civic body or complaint service.

## Privacy and Data Safety

- [ ] Make the privacy policy, in-app disclosure, Play Data Safety answers, and actual
  release behavior agree exactly.
- [ ] Treat off-device transmission as collection even when processing is short-lived.
  Audit at least:
  - selected road-damage photos and Drive/VOD image frames sent to OpenAI; user-confirmed
    Garbage and Manhole photos are not sent to OpenAI;
  - precise coordinates sent to Nominatim (including structured city/state fields used by
    the 31 additional city routes), to Karnataka GIS for Karnataka points, and with
    the GPS-accuracy envelope to official Telangana GIS for exact Hyderabad CURE routing;
    Maharashtra, West Bengal, Punjab, Tamil Nadu (including GCC), Andhra Pradesh, Telangana,
    Delhi NCT, and Ahmedabad boundary checks remain on-device;
  - for eligible Karnataka routes only, road address and procurement shortlist sent to
    OpenAI for probable contract matching; contract matching elsewhere is disabled;
  - API credential and standard network metadata received by external services;
  - the selected regional routing/tender pack or 2° National Highway tile and standard
    connection metadata disclosed to GitHub Pages when that checksum-pinned data is downloaded;
  - map-area tile requests, email-app hand-off, Android sharing, and every included SDK;
  - report text or evidence handed to a selected email, WhatsApp, or share
    destination, plus app/portal, Play-listing, and dialler launch metadata;
  - an official grievance/reference ID stored locally when the user marks an eligible
    official-channel report submitted.
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
  drive summaries, app-held footage, downloaded pack/highway-tile cache, key/name/settings, and the
  app's Documents debug-frame directory. Files copied, shared, attached, or sent elsewhere
  remain under that destination's control.
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
  review API key, manual capture, Drive Mode, History, Karnataka email, and every regional
  handoff. Access details must remain valid, reusable, and available throughout review.
- [ ] If any regional route is hard for an overseas reviewer to exercise, include
  lawful, repeatable review steps and test material that expose both flows without falsifying
  device location or policy declarations.
- [ ] Complete the Government apps declaration as an unaffiliated app that communicates
  government information. Keep easy-to-see official source URLs and the explicit statement
  that the app represents no government body, does not establish road ownership, and does
  not submit a grievance automatically. Identify every official source and complaint channel
  shown in the listing or source page without implying affiliation.
- [ ] Do not add civic-body marks, copied government graphics, or framing that implies
  official status. Keep every external service clearly labelled as a user-controlled handoff.

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
