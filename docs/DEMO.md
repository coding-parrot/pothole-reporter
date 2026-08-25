# Before a demo

Do not quote a fixed latency without measuring the signed current release on the phone and
network used for the demo. Include a cold launch, a state's first pack download, and a warm
run from the verified cache.

## Set up, in this order

1. **Install and open it once, before the room is watching.** Measure the cold launch on
   the target phone; do not infer it from an emulator or an older release.
2. **Paste the OpenAI key** in Settings for Pothole and Drive. Garbage and Manhole work
   without it because the user explicitly selects those categories.
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

Six services, and a demo venue's wifi can break any of them:

- **OpenAI** for pothole detection. Without it, Pothole and Drive cannot detect damage;
  user-confirmed Garbage and Manhole reports still work.
- **GitHub Pages** for a state's first routing/contact-pack download and, only when
  eligible, the optional Karnataka tender pack. After a successful verified download, that
  pack can be read from local cache. If a required routing/contact pack is missing or fails
  its pinned checksum, authority routing stops rather than guessing. If the optional tender
  pack fails, the report continues without contract context.
- **Karnataka's state GIS** for a more-specific urban body and road-class check. If it is
  unavailable, exact statewide containment can still offer neutral Janaspandana, but the app
  never guesses a specific body, owner, or officer.
- **Telangana's official TGRAC GIS** for the specific Hyderabad CURE and Cantonment checks.
  Android requires both live responses for My Cure and fails that specific route closed if
  either is unavailable; neutral statewide Prajavani can still be offered.
- **OpenStreetMap Nominatim** for the street address and the 15 structured-city routes,
  which require exact city/municipality and state fields. Exact polygon routes do not depend
  on the returned place name; Ahmedabad uses the verified cached 48-ward AMC union.
- **OpenStreetMap tiles** for the map on the dashboard. Without them the map renders
  half-blank while the pins and the counts stay correct.

Have a phone hotspot ready. The refusals are correct behaviour but they are not what you
want on stage.

## Questions worth having an answer ready for

- **"Where does the contract data come from?"** KPPP, the state's own procurement portal.
  `docs/SOURCES.md` distinguishes the 42,283-row road-work source snapshot from the
  13,577 supported-body candidates before carriageway filtering. The optional downloadable
  v2 pack contains 5,351 filtered records. Its old 341-row spot-check applies to the full
  source snapshot, not to the reduced pack.
- **"Does it work outside Karnataka?"** Yes: the app covers the full States of Maharashtra,
  West Bengal, Punjab, Karnataka, Kerala, Tamil Nadu, Andhra Pradesh, Telangana, Uttar
  Pradesh, and Chhattisgarh; all 50 largest Census 2011 population centres; Delhi NCT;
  Android-verified official 2,053 km² Hyderabad CURE coverage; a reviewed 48-ward
  Ahmedabad footprint; and mapped National Highways across India.
  Maharashtra-wide coverage uses exact MMR/PMC routes where available and a neutral
  Aaple Sarkar handoff elsewhere. West Bengal keeps the exact KMC route inside KMC and
  uses the neutral state PGRS elsewhere; neither state fallback identifies a road owner,
  district, department, or local body. Uttar Pradesh uses neutral Jansunwai–Samadhan and
  excludes Delhi NCT; Chhattisgarh uses the neutral CM Helpline, with NIDAAN 1100 only as
  an urban civic alternate. Outside exact GCC limits, confidently contained Tamil Nadu points
  use the neutral statewide route; Secunderabad Cantonment is excluded only from the specific
  My Cure route; and the wider AUDA area is excluded from Ahmedabad coverage. Contract
  matching remains Karnataka-only.
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
- **"Where do the photos go?"** Selected road-damage images go directly to OpenAI and may
  be processed outside India. User-confirmed garbage and manhole photos stay local unless
  the user chooses an external handoff. Photos can contain number plates and faces; see the
  privacy policy and in-app disclosure.
- **"Does it send complaints automatically?"** No. Every one is a draft you press send on.

## What not to claim

- Not that statewide Karnataka containment identifies a specific municipal recipient:
  182 of 319 bodies have a published address, while other confidently contained points use
  neutral Janaspandana and must select the department or local body themselves.
- Not that Delhi coverage means the whole NCR: only Delhi NCT is covered. PWD Sewa is a
  cross-agency handoff, not a claim that PWD owns every road.
- Not that statewide West Bengal coverage identifies a local body or road owner. Only the
  verified KMC polygon selects KMC; every other in-state point uses the neutral West Bengal
  PGRS handoff and requires the user to select and verify the district or department.
- Not that Uttar Pradesh or Chhattisgarh containment identifies a local body, road owner,
  complaint category, or department. Their statewide services are neutral external handoffs.
- Not that the specific GCC route covers the whole Chennai metropolitan area: only the
  verified GCC polygon selects GCC; other confidently contained Tamil Nadu points use the
  neutral statewide handoff.
- Not that Hyderabad CURE containment identifies a corporation or road owner: Android checks
  the official CURE and exact Cantonment layers live, while My Cure is shared without
  corporation attribution. Web use or unavailable service responses cannot select My Cure;
  neutral statewide Prajavani may still be offered.
- Not that Ahmedabad coverage means the wider AUDA area: only the reviewed ODbL union of
  48 AMC wards is covered, and containment is not proof of road ownership.
- Not that it produces a ticket number. Karnataka opens an email draft; Maharashtra,
  West Bengal, Delhi, Chennai, Hyderabad, and Ahmedabad open a user-selected official channel.
  Only the external service can return an official grievance/reference ID.
- Not that the contract match is certain. Every complaint says "probable record match,
  kindly verify", and that wording should stay.
