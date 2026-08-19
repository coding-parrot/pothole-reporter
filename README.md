# Pothole Reporter

Around 2,000 people a year die on Indian roads because of potholes, and many more
lose hours to them. Most of those potholes are already someone's job to fix, often
under a contract still in warranty. The gap is that nobody reports them to the right
person with enough detail to act on.

This app closes that gap. Mount your phone, drive, and it finds the potholes,
works out which officer is responsible, finds the road contract they were built
under, and writes the complaint. You read it and press send.

The aim is to get roads repaired, so that fewer people are hurt and fewer hours are
lost sitting in traffic that a good road surface would not have created. It is not
written against anyone. Officers and contractors have a difficult job and a large
city to keep up with; a complaint that arrives with a photograph, exact coordinates,
the right office and the relevant contract is simply easier to act on than one that
does not. That is the whole idea.

**It works in Karnataka today.** The officer directory, the road contracts and the
boundary data are all Karnataka's. A pothole anywhere else is photographed and saved, but
the app will not name a recipient it cannot verify, so it refuses rather than sending your
complaint to the wrong office. Other states are the next piece of work, not a solved one.

**No OpenAI account needed.** Detection runs on a shared service that holds the key, so
reporting a pothole costs you nothing and needs no signup. Bring your own key if you have
one and it is used instead.

![How a photo becomes a complaint](docs/architecture.png)

## A real one it caught

Not an illustration. This photo, this output, from the app on a phone.

<img src="docs/example-pothole.jpg" width="360" alt="Pothole on 17th Main Road, HSR Layout, Bengaluru">

| | |
|---|---|
| Verdict | **medium pothole**, confidence 0.78 |
| Address | 17th Main Road, Sector 3, HSR Layout, Bengaluru, 560102 |
| Routed to | Commissioner, Bengaluru South City Corporation |
| Probable contract | `BBMP/2024-25/RD/WORK_INDENT3877`, SANGAMESH INFRASTRUCTURE |

And the complaint it drafted:

```text
Dear Commissioner, Bengaluru South City Corporation (BSCC),

I would like to report a pothole that needs repair.

Location: 17th Main Road, Sector 3, HSR Layout, Bengaluru, 560102
Coordinates: 12.911500, 77.642700
Map link: https://maps.google.com/?q=12.911500,77.642700
Approximate size: medium

PFA image. This pothole poses a danger to two wheeler riders and other road users. I request the city corporation to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.

Public procurement records indicate this road stretch probably falls under tender BBMP/2024-25/RD/WORK_INDENT3877 ("Pothole Filling Works under Maintenance Works in Ward No. 221-HSR Layout for the year 2024-25 in Bommanahalli Division."), published on 13-09-2024, with SHARANAPPA SANGAMESH( SANGAMESH INFRASTRUCTURE INDIA PRIVATE LIMITED ) recorded as the winning bidder, and it may still be within the maintenance period.

If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.

Thank you for your service to the city.

Regards,
Gaurav Sen
```

That last paragraph is the point. A pothole on a road still under warranty should be
repaired by the contractor at no further cost to the public.

## Use it

1. **Install.** Download `PotholeReporter.apk` from the
   [Releases page](https://github.com/coding-parrot/pothole-reporter/releases), sideload
   it, and allow camera and location. No account, no API key.
2. **Drive.** Mount the phone facing the road and tap Drive Mode. It shoots every 8
   metres and checks eight frames at once, so you just drive.
3. **Or point and shoot.** Tap Report a pothole for a single one you have stopped at.
4. **Read the draft, press send.** Each confirmed pothole becomes an email draft with the
   photo, address, coordinates, officer and probable contract. The app never sends
   anything itself.
5. **See your city.** Your contribution shows what you have reported, and what everyone
   else using the app has reported around you.

Settings has English and Kannada, an optional OpenAI key, and a debug mode that keeps the
drive video so you can re-analyse it later.

## What happens when you find one

The photo goes to the service, which checks it is a road scene before spending anything on
it, then decides whether there is a pothole and how big. Your phone asks the state GIS
which local body owns that stretch, and the service looks up that body's road contracts.

Then one of four things happens, and only the first produces a letter.

- **It is yours to report.** You get a draft addressed to the officer responsible, with
  the photo, the coordinates and the probable contract.
- **Somebody already reported it.** Your sighting is added to theirs and the count of
  people who have seen it goes up. No second letter: two complaints about one hole make
  the first easier to dismiss.
- **Nobody in the app's directory owns it.** A national highway, a rural road, a town with
  no published address, or outside Karnataka. The photo and location are kept, and the app
  says which of those it was rather than guessing an office.
- **It is not a pothole.** Nothing is stored except the count, unless debug mode is on.

## Who receives them

The app asks Karnataka's state GIS which body owns the road, then addresses its head.

- **City corporation** goes to the Commissioner, **council or town panchayat** to the
  Chief Officer. 182 of the state's 319 bodies, including all 18 corporations and the
  five that replaced BBMP in 2025.
- **National highways are refused.** NHAI or the PWD highways division maintains them,
  not the town they cross. About 1,450 km of NH runs inside Karnataka's town boundaries.
- **Rural roads are refused** and name the gram panchayat, rather than guessing an office.
- **Outside Karnataka, or a body with no published address, is refused.** A complaint to
  the wrong office is worse than no complaint.

Email is a contact channel, not a tracked one. For a ticket number, also file on Sahaaya 2.0.

## Contracts

13,577 awarded road-work contracts from KPPP, Karnataka's procurement portal, held by the
service rather than bundled into the app, so they can be refreshed without a release. When
a match clears a confidence gate the complaint names the tender.

- **Only the officer's own works count.** A Commissioner cannot enforce a state PWD,
  panchayat or irrigation contract, so those are never named in their letter.
- **A word is weighed by how rare it is** inside that body's own contracts. The town's own
  name appears in nearly all of them and identifies nothing, so it is discarded outright.
- **No evidence means no contract named.** If nothing in the address matches any of that
  body's work descriptions, the complaint names none rather than guessing.
- **Warranty is inferred from the publication date**, since award records carry no defect
  liability period. It is always stated as a possibility, never a fact.

Every source is documented with commands to verify it in [docs/SOURCES.md](docs/SOURCES.md).

## Cost

Reporting is free to you. Each frame is a call the service pays for, so it protects itself:
a cheap check confirms the photo is a road before the expensive one runs, each install has
a daily allowance, and a monthly ceiling stops a viral week from becoming an unbounded
bill. When that ceiling is reached the service says so and points you at your own key
rather than quietly spending more.

There is no cheap pothole pre-filter. One was tried and it rejected most real potholes, so
it was removed.

## Where this is going

- **Every major Indian city.** Karnataka works today. Mumbai, Delhi, Hyderabad,
  Chennai and Pune each need their own officer directory and tender source, and Delhi
  needs road-ownership data that splits by carriageway width. The remaining 137
  Karnataka bodies need addresses their district sites do not publish.
- **A background camera app.** Capture should not require the app in the foreground
  with the screen awake. That needs a native camera service, which is real work but
  is what makes this usable on an ordinary commute.
- **No API key.** A hosted service so anyone can report a pothole without opening a
  billing account, with the operator's key behind attestation, per-device quotas and
  a spend ceiling. Built, on the `server-backed` branch, not yet live.

## Development

`static/index.html` is the UI and `static/standalone.js` is the engine; `server/` is the
Cloudflare Worker that holds the key, the contracts and the reports.

    ./tools/build-apk.sh          # mirrors, syncs, builds, and refuses a stale APK
    ./tests/run-all.sh            # app: UI text, routing, the highway gate
    cd server && npm test         # worker: signing, quota, gating
    cd server && node test/reports.test.mjs   # dedup, city view, contract match

The worker tests run its real SQL against real SQLite and the real `schema.sql`, so the
queries are exercised rather than mocked. Deployment and the contract import are in
[server/README.md](server/README.md).

To test the app in a browser, serve `android-app/www/` and open it in Chromium with
`--disable-web-security`. Add `?key=sk-...` to exercise the own-key path instead of the
service.

`eval/` holds the detection benchmark and, more usefully, a log of the accuracy changes
that were tried and rejected, with the evidence.

## What leaves your phone

Every photo the app checks is sent to the service, which forwards it to OpenAI, so it is
processed outside India. Road photographs routinely contain number plates, faces and
shopfronts, and none of that is blurred. Coordinates go to OpenStreetMap to resolve an
address and to Karnataka's state GIS to find the responsible body.

When a pothole is confirmed, the service stores a row: where it is, how big, a hash of the
photo, and a pseudonymous id for the install that saw it. That is what makes "somebody
already reported this" and the city map possible.

What it does not store, anywhere: your name, your phone number, your email, an account, or
the photograph itself. The pseudonymous install id is never returned by any read, because
one install's reports over time would be a movement trace even though a single one is not.

If you use your own OpenAI key instead, nothing is stored on the service at all, and the
city map is unavailable to you because there is nowhere for other people's reports to live.

## Disclaimer

Contract matches are probabilistic and always worded as a probable match to verify;
keep that wording. The app never sends email. Every complaint is sent by you, from
your account, and you are responsible for its contents. Not legal advice, and not
affiliated with GBA, BBMP or any government body.

## Credits

Contracts from [KPPP](https://kppp.karnataka.gov.in), the Karnataka Public Procurement
Portal, with contractor names from the public-domain snapshot at
[bengaluru-road-contracts.pages.dev](https://bengaluru-road-contracts.pages.dev).
Boundaries from [KGIS](https://kgis.ksrsac.in), run by KSRSAC. Officer addresses from
district NIC sites and the bodies' own sites. Geocoding by OpenStreetMap Nominatim, maps
by [Leaflet](https://leafletjs.com). Detection and drafting by OpenAI vision models.
Full provenance in [docs/SOURCES.md](docs/SOURCES.md).

## License

MIT. See [LICENSE](LICENSE).
