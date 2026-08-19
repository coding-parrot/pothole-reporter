# Detection service

Lets someone report a pothole without owning an OpenAI account. The operator's key
lives here; the app never sees it.

That means this endpoint spends the operator's money for anonymous callers, so most
of the worker is about refusing to do that for the wrong people.

## The gates, cheapest first

| Gate | Rejects | Cost of rejecting |
|---|---|---|
| Shape | not a data URL, over 3.5 MB | nothing |
| Identity | unsigned, forged, stale (>5 min) or replayed | nothing |
| Integrity | not the real app on a real device | one Google call |
| Budget | device past its daily quota, or the month's cap reached | nothing |
| Road check | photos that are not road scenes | one `gpt-5-nano` call |
| Detection | — | one `gpt-5-mini` call |

**Signing is not authentication.** Anyone can generate a keypair, so a signature only
proves the caller is the same one as last time. That is enough to make a per-device
quota mean something and to revoke a device, and it is *not* enough to stop a
determined attacker. Play Integrity is what does that.

**Play Integrity is optional and off until configured.** It only returns a useful app
verdict once the app ships through Play, and the service has to work before that. When
it is unconfigured, unattested devices get a tenth of the daily quota, so an
unattested caller cannot cost much.

## Deploying

```bash
cd server
npm install
npx wrangler kv namespace create DEVICES     # put the id in wrangler.toml
npx wrangler secret put OPENAI_API_KEY
npx wrangler deploy
```

Optional, once the app is on Play:

```bash
npx wrangler secret put PLAY_INTEGRITY_PROJECT   # projects/123456789
npx wrangler secret put PLAY_INTEGRITY_TOKEN
```

## Cost ceiling

`MONTHLY_IMAGE_CAP` in `wrangler.toml` is the thing that makes a viral week survivable.
Each image costs one `gpt-5-nano` road check plus, if it passes, one `gpt-5-mini`
detection. At the current defaults 50,000 images a month is roughly the ceiling worth
budgeting for; when it is reached the service refuses and tells users to add their own
key, rather than quietly billing more.

## Tests

```bash
npm test
```

Runs the worker's real logic against an in-memory KV and a stubbed OpenAI, covering
forged and replayed signatures, stale timestamps, oversized images, the road gate
firing before the expensive call, and a device running out of allowance.

## Keeping the two builds honest

The worker's `DETECT_PROMPT` and `ASSESS_SCHEMA` are byte-identical to the app's in
`static/standalone.js`, and they must stay that way. A user on the hosted service and a
user with their own key are running the same product, and two prompts would mean two
accuracies in one app, with no way for either user to know which they got. The confidence
gate lives only in the app, so there is one gate in one place; the worker returns the raw
verdict.

Verified before this branch shipped: the cheap `gpt-5-nano` road gate accepted 18 of 18
real road photos in `eval/images/seed`, including every dashcam frame. That matters
because a false negative there is invisible to the user: their pothole is refused before
the detector ever sees it.

One difference that is deliberate. Drive Mode on a personal key streams the response and
stops as soon as a frame is known to be rejected. The service returns a finished verdict,
so service users do not get that saving. Streaming it through the worker would restore it.

## What the service holds

Three tables, in `schema.sql`. A row is a road defect and the pseudonymous install that
saw it. There is no name, no phone, no email and no account anywhere in it, and
`device_id` is written but never returned by any read endpoint, because one install's
reports over time are a movement trace even though a single one is not.

- `POST /v1/report` records a confirmed pothole and answers whether somebody already
  reported it. Two reports within `DEDUPE_METRES` (20 m) and `DEDUPE_DAYS` (120 days) are
  one pothole. The bounding box is widened by 1/cos(latitude), or the search would be
  narrower east to west than north to south, and the exact circle is applied afterwards
  because a box is wider than a circle at its corners.
- `GET /v1/city?lgd=` returns everything reported in one local body, or `?lat=&lng=` with
  a radius when there is no body code.
- `GET /v1/tender?address=&lgd=` does the contract match that used to run on the phone.

`seen_count` means how many separate installs saw a pothole, which is why confirmations
are a separate table with a primary key on (report, device): one person cannot raise it by
reporting twice, and the install that created a report is recorded as having seen it, or
its own second report would read as a second person.

## Loading the contracts

    node tools/import-tenders.mjs ../data/tenders-karnataka.json > /tmp/tenders.sql
    wrangler d1 execute pothole --file=/tmp/tenders.sql --remote

13,577 citable contracts of 42,283. The rest belong to bodies with no published address or
to agencies a municipal officer cannot enforce against, so they could never be named.

## Deploying

    wrangler d1 create pothole            # put the id in wrangler.toml
    wrangler kv namespace create DEVICES  # put the id in wrangler.toml
    wrangler d1 execute pothole --file=schema.sql --remote
    wrangler secret put OPENAI_API_KEY
    wrangler deploy
