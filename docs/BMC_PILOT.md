# Mumbai Pothole Evidence: Independent Handoff and Proposed BMC Pilot

**Status:** Proposal only. Pothole Reporter has no BMC contract, endorsement, data-sharing arrangement, API access, or official integration. It must not use BMC logos or imply affiliation without written permission.

## Two distinct tracks

### Immediate independent citizen handoff

Until BMC authorizes an integration, any Mumbai mode must remain citizen-controlled:

1. A mounted phone or passenger captures the road; nobody interacts with the app while driving.
2. The app detects and locally groups likely repeat observations.
3. While stopped, the user reviews the image, location, damage type, and wording.
4. The user manually submits the evidence through a public channel identified by BMC. Its official [MARG/Pothole Grievance dashboard](https://marg.mcgm.gov.in/BMC_C_Pothole_Dashboard/) lists Pothole QuickFix, WhatsApp, 1916, and Twitter as grievance sources.

This track creates no official ticket itself. It uses no private BMC interface, bulk submission, automatic government routing, BMC branding, or claim that a contractor or warranty is responsible. If road ownership is uncertain, the app says so rather than guessing.

### Future authorized pilot and integration

After a signed pilot letter or MoU, Pothole Reporter could provide structured, deduplicated evidence to BMC's existing workflow. It would not replace MARG/Pothole QuickFix. BMC-authorized ward/agency routing and official grievance status would exist only in this pilot track.

## Proposed 30-day, two-ward pilot

**Scope:** Two wards jointly selected for different traffic, surface, and ownership conditions; up to 20 trained participants or vehicles; 300-500 human-reviewed candidate events. Live grievances start only with written BMC approval and remain subject to a daily cap and stop procedure.

| Period | Work | Deliverable |
| --- | --- | --- |
| Days 1-3 | Lock fields, references, consent, security, retention, cap, and contacts. | Signed protocol; sandbox or approved batch schema. |
| Days 4-10 | Shadow mode; validate without live grievances unless expressly authorized. | Error sample, calibrated thresholds, routing baseline. |
| Days 11-25 | Controlled live or officer-reviewed intake; deduplicated, rate-limited, and approved by a stopped user or reviewer. | Receipt IDs where available, or reconciliation export. |
| Days 26-30 | Reconcile detections, clusters, routing, intake, and status. | Results, limitations, risks, and go/no-go recommendation. |

BMC may pause intake immediately. Repair time is an observed civic outcome, not a promised product result.

## Proposed acceptance measures

These are targets for negotiation, not current performance claims. BMC and the developer must approve the sample and adjudication method first.

| Measure | Proposed threshold |
| --- | --- |
| Detection precision | At least 85% of submitted detections accepted by the agreed review panel, with sample size and confidence interval. |
| Duplicate control | At least 80% of agreed-window repeats merged; fewer than 5% of reviewed clusters falsely merge distinct defects. |
| Routing accuracy | At least 95% correct ward/agency where an authoritative BMC record exists; uncertain records withheld. |
| Evidence completeness | At least 95% contain all required fields, including image, coordinates, time, class, consent, and idempotency key. |
| Interface reliability | At least 98% accepted API submissions or valid batch rows; no duplicate official grievance caused by retry. |
| Processing latency | 95th percentile below 60 seconds from approval to pilot-bridge acceptance; BMC acknowledgement measured separately. |
| Privacy and safety | 100% opt-in; no required driver interaction; documented deletion; no unresolved critical privacy/security incident. |

The final report must also disclose false positives, known misses from a separately surveyed ground-truth sample, false merges, unsupported roads, rejections, and unresolved ownership.

## Exact asks from BMC

### Interface and reference data

- Non-production MARG/Pothole QuickFix endpoint and test account, or an approved CSV/JSON schema and named officer-review queue.
- Required fields and taxonomy; image limits; authentication; rate limits; idempotency; acknowledgement/error codes; and retry rules.
- Webhook or periodic export with grievance ID, ward/agency, status history, rejection reason, and timestamps.
- Authoritative ward boundaries, BMC road IDs, and ownership/routing matrix covering BMC and other road agencies.
- If contract checking is in scope, a legally shareable, versioned extract with road ID or geometry, award/work-order ID, contractor, dates, status, and actual DLP or maintenance terms. Officer verification remains mandatory.
- Named Roads & Traffic, IT, ward, privacy/security, and grievance contacts; intake cap; escalation path; and kill switch.

### Pilot letter, MoU, and data schedule

The documents must define wards and dates; operational sponsor; no-cost or funded status; controller/processor roles; lawful basis and notice; permitted uses; hosting and subprocessors; security; retention/deletion; breach notification; audit; service contacts; cap; termination; liability; and publication approval. They must state that the pilot creates no production award, preferred-vendor status, exclusivity, or commitment to procure.

## Current road-contract data blocker

**No safe official feed is available to the app today.** As of 21 August 2026, the official [BMC tender page](https://portal.mcgm.gov.in/irj/portal/anonymous/qletenders_new?guest_user=english) and [MahaTenders](https://mahatenders.gov.in/nicgep/app) expose notices and documents, but the project has not identified or received a documented, stable, machine-readable BMC award feed linking current road IDs or geometry to contractor, work-order dates, status, and actual DLP terms.

Tender PDFs are not a complete current award register. Scraping them could misidentify ownership or liability. Mumbai contract matching must stay disabled until BMC supplies or approves a versioned feed, schema, reuse terms, update cadence, and road linkage. Any match remains advisory and officer-verified.

## Architecture and consent boundary

The public Android app remains pure-client: capture, review, detection, and history operate on the phone, with no project-operated account or collection backend. Independent handoff needs no bridge.

Only opted-in pilot users would enable a separate bridge:

```text
mounted phone or passenger capture
  -> stopped-user or designated-reviewer approval
  -> pilot bridge: validation, rate limiting, cross-user deduplication
  -> BMC-approved API, batch queue, or officer review
  -> official grievance ID/status reconciliation
```

Only the accepted still image and approved fields transfer. Raw drive video, unrelated frames, personal API keys, contacts, and non-pilot history are excluded. Encryption, least privilege, audit logs, secret rotation, retention/deletion, hosting location, model processing, subprocessors, and any cross-border transfer require written approval before live intake.

## Proposed data and IP terms

- Subject to law and contributor consent, BMC owns and controls pilot grievance/project data delivered to or created in its official systems, including official IDs, routing decisions, status history, and operational reports.
- The developer retains all background IP: core detector, public repository, model orchestration, prompts, generic deduplication, libraries, tools, documentation, pre-existing know-how, and generic improvements that disclose no BMC confidential information.
- BMC-specific adapters, schemas, configuration, and reports carry only rights expressly granted in the pilot or later contract. No source assignment, exclusivity, production licence, escrow, or branding right is implied.
- Production hosting, support, integration, licences, certification, SLAs, and source/escrow rights require separate procurement terms.
- Public results must be aggregated and de-identified. Use of BMC's name, quotes, screenshots, grievance data, or claimed outcomes requires written approval.

## Procurement and live-pilot prerequisites

1. Written BMC sponsor, two ward owners, integration method, intake cap, and stop procedure.
2. Executed pilot/MoU and data-processing/security schedules before any pilot transfer.
3. Approved ward/road ownership data and, if used, the award/DLP feed described above.
4. Approved bridge hosting, service credentials and budget, security test, incident contact, and retention/deletion implementation.
5. Trained participants, consent materials, independent ground-truth review, and safe field protocol.
6. [BMC vendor registration](https://www.mcgm.gov.in/irj/portal/anonymous/qlVendorApp?guest_user=english), [MahaTenders enrolment](https://mahatenders.gov.in/nicgep/app), tender-required Class-III signing/encryption certificate, tax/bank documents, and tender-specific qualifications. BMC's [vendor FAQ](https://www.mcgm.gov.in/irj/go/km/docs/documents/Vendor/FAQ.pdf) advises applying about one month before a tender closes.

Production requires the applicable BMC procurement. The prior [2024 pothole-system support tender](https://portal.mcgm.gov.in/irj/go/km/docs/documents/Tenders/ETH/ETH_8000068096_050624.pdf) and [2023 road-digitization/AI RFP](https://portal.mcgm.gov.in/irj/go/km/docs/documents/Tenders/ETH/ETH_8000043263_210623.pdf) show historical requirements only; a future solicitation controls eligibility and terms.

## Official context and independent coverage

- [BMC Roads & Traffic office](https://portal.mcgm.gov.in/irj/portal/anonymous/qlroadmainoffice?guest_user=english)
- [Participate Mumbai civic-engagement/CSR portal](https://participatemumbai.mcgm.gov.in/csr)
- [BMC vendor training and helpdesk guidance](https://portal.mcgm.gov.in/irj/portal/anonymous/qlvendortrain?guest_user=english)
- Independent coverage: [Times of India](https://timesofindia.indiatimes.com/education/news/this-bengaluru-engineer-has-built-an-ai-system-that-spots-potholes-and-tracks-who-must-fix-them/amp_articleshow/133290004.cms), [NewsBytes](https://www.newsbytesapp.com/news/science/bengaluru-engineer-gaurav-sen-builds-ai-app-to-locate-potholes/tldr), and [News Karnataka](https://newskarnataka.com/bengaluru/bengaluru-engineer-uses-ai-to-detect-potholes-and-file-civic-complaints/16082026/)

Media coverage shows public interest; it is not an accuracy audit, BMC endorsement, procurement qualification, or evidence of repair outcomes.
