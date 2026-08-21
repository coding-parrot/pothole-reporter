# Before a demo

Do not quote a fixed latency without measuring the signed current release on the phone and
network used for the demo. Include a cold launch, a state's first pack download, and a warm
run from the verified cache.

## Set up, in this order

1. **Install and open it once, before the room is watching.** Measure the cold launch on
   the target phone; do not infer it from an emulator or an older release.
2. **Paste the OpenAI key** in Settings. Nothing detects without it.
3. **Set your name** in Settings. Without it, complaints are signed "A concerned citizen".
4. **Take one report in the area you will demonstrate.** It proves the key works and
   downloads and verifies that state's routing pack. Delete the report afterwards if you
   want a clean history; do not use **Delete all app data**, because that also clears the
   verified pack cache.

## What to measure

Record cold launch, single-photo detection, first routing-pack download, warm cached
routing, and Drive Mode processing on the actual demo setup. Model, network, phone, and
whether a pack is already cached all affect elapsed time. The current UI reports subtype
plus clear/probable/uncertain/absent and does not display an uncalibrated model percentage.

## What needs the network, and what happens without it

Five services, and a demo venue's wifi can break any of them:

- **OpenAI** for detection. Without it, nothing is detected and the app says so.
- **GitHub Pages** for a state's first routing/contact-pack download and, only when
  eligible, the optional Karnataka tender pack. After a successful verified download, that
  pack can be read from local cache. If a required routing/contact pack is missing or fails
  its pinned checksum, authority routing stops rather than guessing. If the optional tender
  pack fails, the report continues without contract context.
- **Karnataka's state GIS** for the officer and the highway check. Without it, the app
  refuses to name anyone and says the road could not be checked. It does not guess.
- **OpenStreetMap Nominatim** for the street address and, in Ahmedabad only, the required
  exact structured `city` or `municipality` routing match. Without that exact Ahmedabad
  result, routing fails closed even when the point is inside the local relevance envelope.
- **OpenStreetMap tiles** for the map on the dashboard. Without them the map renders
  half-blank while the pins and the counts stay correct.

Have a phone hotspot ready. The refusals are correct behaviour but they are not what you
want on stage.

## Questions worth having an answer ready for

- **"Where does the contract data come from?"** KPPP, the state's own procurement portal.
  `docs/SOURCES.md` distinguishes the 42,283-row road-work source snapshot from the
  13,577 supported-body candidates in the optional downloadable pack. Its old 341-row
  spot-check applies to the full source snapshot, not to the reduced pack.
- **"Does it work outside Karnataka?"** Yes: the app covers Delhi NCT, the current MMR,
  Pune and Kolkata Municipal Corporation limits, Greater Chennai Corporation limits, a
  conservative partial Hyderabad-core outline, and strict structured Ahmedabad matches.
  It suggests a complaint route but does not claim road ownership. The wider NCR, PCMC,
  Kolkata's neighbours, the wider Chennai metro, the published Secunderabad Cantonment
  extent, and ambiguous Ahmedabad results are excluded. Contract matching remains
  Karnataka-only.
- **"Why is the APK smaller?"** State routing/contact datasets are versioned downloads,
  and Karnataka tenders are a separate optional download. The app verifies each file
  against a pinned checksum and caches it locally; on a subsequent pack use, entries past
  their unused limits are pruned. Adding a state does not embed that state's large data in
  every APK.
- **"What does the pack host learn?"** GitHub Pages receives ordinary connection
  metadata, including the IP address and a URL that names the state. The pack request
  contains no report, photo, or exact coordinates.
- **"What about highways?"** Municipal routing is refused because the app cannot verify
  that a supported civic body owns or maintains the road.
- **"Where do the photos go?"** Selected images go directly to OpenAI and may be processed
  outside India. Road photos can contain number plates and faces; see the privacy policy
  and the in-app disclosure.
- **"Does it send complaints automatically?"** No. Every one is a draft you press send on.

## What not to claim

- Not that it covers all of Karnataka: 182 of 319 bodies have a published address, and
  rural and PWD roads are refused.
- Not that Delhi coverage means the whole NCR: only Delhi NCT is covered. PWD Sewa is a
  cross-agency handoff, not a claim that PWD owns every road.
- Not that Kolkata coverage means the whole metropolitan area: only current KMC limits are
  covered, using a verified downloaded copy of the official West Bengal UDMA boundary.
- Not that Chennai coverage means the whole metropolitan area: only the verified GCC
  polygon is covered.
- Not that the Hyderabad outline is any current corporation boundary: coverage is partial,
  the Cantonment extent is refused, and My Cure is shared without corporation attribution.
- Not that Ahmedabad uses an AMC boundary: it requires an exact structured geocoder result
  inside a relevance envelope, and neither is proof of municipal containment.
- Not that it produces a ticket number. Karnataka opens an email draft; Maharashtra,
  Kolkata, Delhi, Chennai, Hyderabad, and Ahmedabad open a user-selected official channel.
  Only the external service can return an official grievance/reference ID.
- Not that the contract match is certain. Every complaint says "probable record match,
  kindly verify", and that wording should stay.
