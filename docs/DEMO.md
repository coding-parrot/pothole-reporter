# Before a demo

Routing and cold-start figures were measured on a device on 20 August 2026 with v1.6.2.
The v3 accuracy pipeline now uses bursts and multi-image requests, so its detection
latency must be re-measured on the target phone before quoting it in a demo.

## Set up, in this order

1. **Install and open it once, before the room is watching.** Cold start took about 14
   seconds on an emulator, most of it loading the engine. It is faster on a real phone and
   faster again on a second launch, but the first launch is the slow one.
2. **Paste the OpenAI key** in Settings. Nothing detects without it.
3. **Set your name** in Settings. Without it, complaints are signed "A concerned citizen".
   The name in the README example is whatever you type here.
4. **Take one report before you present.** It warms the connection and proves the key
   works. Delete it afterwards if you want a clean history.

## What it will do, and how long it takes

| | |
|---|---|
| Single shot, photo to finished draft | about 12 to 13 seconds on a device |
| Verdict on screen | about 2 seconds, before the rest |
| Drive Mode, one three-frame event | depends on model/network, six requests at a time |
| Capture spacing | target 6 m; captured events queue while requests are busy |

The HSR Layout example in the README is historical evidence, not an accuracy benchmark.
The current UI reports subtype plus clear/probable/uncertain/absent and does not display
an uncalibrated model percentage.

## What needs the network, and what happens without it

Four services, and a demo venue's wifi can break any of them:

- **OpenAI** for detection. Without it, nothing is detected and the app says so.
- **Karnataka's state GIS** for the officer and the highway check. Without it, the app
  refuses to name anyone and says the road could not be checked. It does not guess.
- **OpenStreetMap Nominatim** for the street address.
- **OpenStreetMap tiles** for the map on the dashboard. Without them the map renders
  half-blank while the pins and the counts stay correct.

Have a phone hotspot ready. The refusals are correct behaviour but they are not what you
want on stage.

## Questions worth having an answer ready for

- **"Where does the contract data come from?"** KPPP, the state's own procurement portal.
  `docs/SOURCES.md` has the exact request and the check that 341 of the portal's first
  1,000 awarded works appear in the bundle byte-identical.
- **"Does it work outside Karnataka?"** Yes: the app covers Delhi NCT, the current MMR
  extent, and Pune and Kolkata Municipal Corporation limits. It suggests a complaint route
  but does not claim road ownership. The wider NCR, PCMC, Howrah, Bidhannagar/Salt Lake,
  and New Town are excluded, and contract matching remains Karnataka-only.
- **"What about highways?"** Refused since v1.6. NHAI or the PWD highways division owns
  them, not the town they pass through.
- **"Where do the photos go?"** To OpenAI, so outside India. Road photos can carry number
  plates and faces. This is in the README and in the app's own settings screen.
- **"Does it send complaints automatically?"** No. Every one is a draft you press send on.

## What not to claim

- Not that it covers all of Karnataka: 182 of 319 bodies have a published address, and
  rural and PWD roads are refused.
- Not that Delhi coverage means the whole NCR: only Delhi NCT is covered. PWD Sewa is a
  cross-agency handoff, not a claim that PWD owns every road.
- Not that Kolkata coverage means the whole metropolitan area: only current KMC limits are
  covered, using the bundled official West Bengal UDMA boundary.
- Not that it produces a ticket number. Karnataka opens an email draft; Maharashtra,
  Kolkata, and Delhi open a user-selected official channel. Only the external service can return an
  official grievance/reference ID.
- Not that the contract match is certain. Every complaint says "probable record match,
  kindly verify", and that wording should stay.
