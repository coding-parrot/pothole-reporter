# Where the app's data and judgments come from

This document records provenance, transformations, and limits. The app combines a
user's camera and phone location with government reference data, OpenStreetMap data,
local matching rules, and AI-generated judgments. A source record is not proof that a
particular defect, authority, or contract match is correct. The user must review the
photo, routing, and any probable contract match before sending a complaint.

## Versioned state packs

Large routing, contact, and procurement datasets are not embedded in the APK. The app
ships a small manifest that pins each supported resource's version, HTTPS URL, byte
length, and SHA-256 checksum. Routing/contact data is published as a separate pack for
Delhi, Gujarat, Karnataka, Maharashtra, Tamil Nadu, Telangana, and West Bengal. Karnataka
procurement data is a separate optional tender pack and is requested only for an eligible
Karnataka contract match.
The Gujarat pack contains routing rules and a relevance envelope, not an AMC polygon.

The app downloads a pack from this project's GitHub Pages site, verifies the complete
response against the manifest before parsing it, and caches only verified bytes on the
device. Missing, malformed, truncated, or checksum-mismatched required routing/contact
data makes the affected route fail closed. A geocoder place name is not a fallback for a
polygon route; Ahmedabad is the documented exception and requires an exact structured
city-or-municipality result as part of its routing rule. Failure of the optional Karnataka
tender pack does not block the report—it continues without contract context. A verified
pack can be read locally after its first
successful download. On a subsequent pack use, caches past their unused limits are pruned
automatically, and **Delete all app data** removes the entire cache immediately.

The pack URL names the state, so GitHub Pages can receive the device's IP address,
standard connection metadata, and a coarse indication of the requested state. No report,
road photo, or exact coordinates are included in the pack request. Adding another state
adds hosted data rather than continually increasing the core APK.

## Maharashtra boundaries and complaint handoffs

### Mumbai Metropolitan Region

The coverage target follows MMRDA's [current official MMR extent](https://www.mmrda.maharashtra.gov.in/en/about-us/about-mmr)
and its [2019 extension notification](https://www.mmrda.maharashtra.gov.in/sites/default/files/2021-09/MMR_Extension_Notification_0.pdf).
That extent contains 19 urban local bodies:

- 9 municipal corporations: Greater Mumbai, Thane, Kalyan-Dombivli, Navi Mumbai,
  Ulhasnagar, Bhiwandi-Nizampur, Vasai-Virar, Mira-Bhayandar, and Panvel;
- 9 municipal councils: Ambarnath, Kulgaon-Badlapur, Matheran, Karjat, Khopoli, Pen,
  Uran, Alibag, and Palghar; and
- Khalapur Nagar Panchayat.

The remaining notified rural MMR is also covered, but routes to the neutral
[Aaple Sarkar grievance service](https://grievances.maharashtra.gov.in/en) rather than to
the nearest city. The Maharashtra routing pack contains a locally evaluated aid made from OpenStreetMap
relation 13312356 plus the Palghar, Vasai, Alibag, Pen, Panvel, and Khalapur taluka
relations named by the notification. OpenStreetMap geometry can be stale or imprecise;
the notification controls the intended scope. In particular, the 2019 notification
excludes declared Scheduled Areas from the added taluka remainders, while this downloaded
outline uses whole OSM taluka polygons and does not subtract those enclaves. Its measured
area is about 6,264.9 km² versus MMRDA's published 6,328 km². It is therefore a practical
fail-safe routing outline, not an exact or legal representation of the notified boundary.
Points that rely only on this outer/rural outline never select a city and use Aaple Sarkar.

Within that outline, valid OpenStreetMap administrative polygons can suggest BMC, Thane,
Kalyan-Dombivli, Navi Mumbai, Ulhasnagar, Bhiwandi-Nizampur, Vasai-Virar, Mira-Bhayandar,
Panvel, Ambarnath, or Kulgaon-Badlapur. BMC uses the union of its 24 ward relations and can
show a qualified ward clue. Matheran, Karjat, Khopoli, Pen, Uran, Alibag, Palghar, and
Khalapur have only place points—not usable civic boundaries—in the source snapshot, so
they deliberately use Aaple Sarkar. Rural points and overlapping polygons do the same. A
Nominatim place name may be displayed as a clue but never selects a recipient. No boundary
proves road ownership: MMRDA, MSRDC, PWD, NHAI, CIDCO, or another agency may maintain it.

### Pune Municipal Corporation

Pune coverage uses the `PMC_Boundary` layer published by the
[official PMC GIS](https://iwmsgis.pmc.gov.in/BP_Docs/index.html) through its
[GeoServer WMS](https://iwmsgis.pmc.gov.in/geoserver/pmc/wms). A copy of the geometry is
included in the verified Maharashtra routing pack for local point-in-polygon checks from the corresponding
[WFS GeoJSON layer](https://iwmsgis.pmc.gov.in/geoserver/pmc/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=pmc:PMC_Boundary&outputFormat=application/json&srsName=EPSG:4326).
It covers the current Pune Municipal Corporation boundary only; Pimpri-Chinchwad
Municipal Corporation (PCMC) and places outside PMC are not included. The app offers PMC
Road Mitra, with PMC CARE as an alternate handoff.

### What a handoff proves

Pothole Reporter prepares evidence; it does not log in, bypass OTP, call a complaint-write
API, press Send, or read complaint status. Opening an app or portal records only “handoff
opened.” Share, WhatsApp, Call, and opening an email draft also do not prove submission.
Where the report uses an official app or portal, it can be marked submitted only after the
user records the grievance/reference ID returned by that service. The user must review and
complete every complaint in the external channel.

For Greater Mumbai, [Pothole QuickFix](https://play.google.com/store/apps/details?id=com.bmc.potholequickfix)
identifies BMC as its developer. BMC also publishes MyBMC WhatsApp at **+91 89992 28999**
and the **1916** civic helpline. The app opens those channels only after the user chooses
the action. BMC's website policy at
`https://portal.mcgm.gov.in/irj/portal/anonymous/privacy?guest_user=english` is why the app
does not frame BMC pages, copy BMC marks, or claim affiliation.

Maharashtra contract matching is disabled. The project has no current, authoritative,
road-linked award and defect-liability feed covering the MMR or PMC, so it does not guess
a contractor, warranty, or road owner from general tender notices.

## Kolkata Municipal Corporation boundary and complaint handoffs

Kolkata coverage uses the `SMARTCITY:wb_municipal_boundary` layer from the Government of
West Bengal Urban Development & Municipal Affairs Department's
[Nagar GIS WFS](https://nagargispariseva.wb.gov.in/geoserver/SMARTCITY/ows?service=WFS&version=2.0.0&request=GetFeature&typeNames=SMARTCITY%3Awb_municipal_boundary&outputFormat=application%2Fjson&srsName=EPSG%3A4326&CQL_FILTER=ULB_Code%3D%27250299%27).
The feature was retrieved on 21 August 2026 and is identified by `ULB_Code 250299` and
`MUN_ID 250299_0000001`. Its source geometry contained one self-intersection, so the
published copy was repaired with GDAL `ogr2ogr -makevalid`, written as RFC 7946 GeoJSON at
seven-decimal coordinate precision, and validated; it was not topologically simplified.
The runtime pins the repaired geometry SHA-256
`fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5` and fails closed
if the downloaded polygon changes without a reviewed code-and-data release.
The WFS advertises no fees or access constraints but publishes no explicit reuse licence.
The geometry is used locally for point-in-polygon checks and is not a road-ownership map.

Coverage is limited to current Kolkata Municipal Corporation limits. Howrah,
Bidhannagar/Salt Lake, New Town, and other neighbouring civic bodies are outside this
route. A place name from Nominatim is contextual only and cannot select KMC.

The primary handoff is [KMC Grievance 2.0](https://kmc.wb.gov.in/citizen/language-selection),
which requires the user to continue through KMC's external login and complaint flow. The
[official KMC Android app](https://play.google.com/store/apps/details?id=com.kmc.app) is an
alternate. KMC also publishes WhatsApp at **+91 83359 88888** and the civic helpline
**1800 345 3375** on its [contact page](https://www.kmcgov.in/KMCPortal/jsp/KmcContact.jsp).
Opening any channel is not submission. The user must complete the complaint externally
and enter the KMC grievance/reference ID before marking the local report submitted.

Contract matching is disabled for Kolkata. The app has no authoritative, road-linked KMC
award, maintenance, or defect-liability feed and does not infer that KMC owns a road merely
because the point is inside its municipal boundary.

## Delhi NCT boundary and complaint handoffs

Delhi coverage is the full National Capital Territory, not the wider National Capital
Region. Noida, Gurugram, Ghaziabad, Faridabad, and other NCR cities outside Delhi NCT are
excluded. The Delhi routing pack contains the polygon for
[OpenStreetMap relation 1942586](https://www.openstreetmap.org/relation/1942586), retrieved
through Nominatim on 21 August 2026 under the ODbL. Its measured area is 1,483.885 km²,
consistent with Delhi's published 1,483 km² extent. The runtime pins SHA-256
`3462ba68bdbbc1fdebc99403aa9e1f9db5e0b78e30ca138b2d25df7463506ab3` over the geometry
and fails closed if the pack is missing, malformed, or replaced without a code release.
`tools/build-delhi-coverage.py` reproduces the routing data. The official
[Delhi State GIS map](https://stategisportal.nic.in/stategisportal/Home/Map/7) is the scope
reference; its access token is not copied into the app.

The polygon answers only whether a point is inside Delhi NCT. It does not identify a road
owner. Delhi roads can be maintained by PWD, MCD, NDMC, Delhi Cantonment, DDA, NHAI,
DSIIDC, DMRC, DJB, or another agency, and municipal containment cannot distinguish them.
The app therefore does not bundle restricted MCD ward maps or guess an owner from a civic
area.

Every accepted Delhi point is offered the Government of NCT of Delhi's
[PWD Sewa complaint flow](https://www.pwddelhi.gov.in/sewa/complaint). PWD Sewa's current
dashboard records road and pothole grievances and forwarding to other Delhi agencies, so
it is used as a coordination handoff rather than labelled as the confirmed owner. The
official Android package is `com.sis.pwdsewaapp`; the app also offers the published
WhatsApp number **+91 81301 88222** and helpline **1908**. The cross-department
[Delhi PGMS](https://pgms.delhi.gov.in/) is the alternate. Opening any channel is not a
submission; the citizen must complete the external complaint and record its official
reference ID.

Contract matching is disabled for Delhi because the project has no authoritative,
road-linked award, maintenance, and defect-liability feed for all Delhi road agencies.

## Greater Chennai Corporation boundary and complaint handoffs

The Tamil Nadu routing pack contains the polygon from
[OpenStreetMap relation 1766358](https://www.openstreetmap.org/relation/1766358), retrieved
through this [Nominatim lookup](https://nominatim.openstreetmap.org/lookup?osm_ids=R1766358&format=jsonv2&polygon_geojson=1&polygon_threshold=0.00001)
on 21 August 2026 and redistributed under the
[Open Database Licence (ODbL)](https://www.openstreetmap.org/copyright). The app evaluates
that downloaded polygon locally. The published geometry measures about 433.098 km² and
has SHA-256 `88f13a9949f34b9c7aa9973db2b7f00659839ef3d434454208314d4479cd6cd5`.
It was reviewed against Greater Chennai Corporation's
official 2025 [`EDP_zoneBoundary_2025`](https://gisgcc.chennaicorporation.gov.in/server/rest/services/GCCDepts/EDPMobile2025/FeatureServer/1)
feature layer. That official service publishes no copyright text or explicit reuse licence,
so its geometry is validation evidence only and is not redistributed by this project.

Coverage is GCC only, not the wider Chennai Metropolitan Area. Adjacent urban bodies are
excluded. Containment does not establish that GCC owns or maintains a road. The primary
handoff is [GCC Public Grievance](https://erp.chennaicorporation.gov.in/pgr/citizen/BeforeReg.do);
the published Android package is
[`com.ceedeev.grivenancev2`](https://play.google.com/store/apps/details?id=com.ceedeev.grivenancev2).
The app also offers GCC's published WhatsApp number **+91 94450 61913** and helpline
**1913**. Opening any channel is not submission, and contract matching is disabled.

## Hyderabad core boundary and shared complaint handoff

The Telangana routing pack contains a conservative polygon from
[OpenStreetMap relation 7868535](https://www.openstreetmap.org/relation/7868535), retrieved
through this [Nominatim lookup](https://nominatim.openstreetmap.org/lookup?osm_ids=R7868535&format=jsonv2&polygon_geojson=1&polygon_threshold=0.00001)
on 21 August 2026 and redistributed under the ODbL. The app checks it locally. This is
partial Hyderabad-core coverage, not a representation of the current Core Urban Region or
a current municipal-corporation map. The published geometry measures about 610.897 km² and
has SHA-256 `6d5ef9edbf927d4037a104d12fe490630b979fbbcbfcfd948550b1d93217de31`.

That OSM outline overlaps part of Secunderabad Cantonment. The pack therefore pins the
published extent of TGRAC's official
[`Cantonment Boundary` layer](https://tgrac.telangana.gov.in/arcgis/rest/services/Hydra_Folder/Administrative_Layer/MapServer/1)
and refuses the whole extent before testing the OSM polygon. Only the four-number extent is
stored; the unlicensed government polygon is not redistributed. This intentionally excludes
some neighbouring civic locations as well as the Cantonment, which is safer than assigning a
Cantonment point to My Cure. A narrower exclusion needs a current reusable boundary or a
confirmed cross-authority grievance route.

Telangana's [G.O.Ms.No.55 dated 11 February 2026](https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/GO.Ms.No.55_11-02-2026.pdf),
linked from the official [BuildNow government-order register](https://buildnow.telangana.gov.in/go-and-act/),
constituted Greater Hyderabad, Cyberabad, and Malkajgiri as three separate municipal
corporations. The OSM relation does not encode that three-way 2026 attribution. The app
therefore does not label an accepted point as belonging to any one of them. It offers the
shared [My Cure](https://play.google.com/store/apps/details?id=cgg.gov.ghmc) civic-grievance
handoff, whose current listing expressly includes pothole grievances, plus its
[web flow](https://igs.ghmc.gov.in/operator/send_otp_mobile). Points outside the conservative
polygon are not routed. Neither containment nor the shared handoff proves road ownership;
contract matching is disabled.

## Ahmedabad structured match and AMC complaint handoff

The Gujarat pack deliberately contains no Ahmedabad municipal polygon. Its reviewed place
identity is [OpenStreetMap node 245711197](https://www.openstreetmap.org/node/245711197),
retrieved through this [structured Nominatim search](https://nominatim.openstreetmap.org/search?city=Ahmedabad&state=Gujarat&country=India&format=jsonv2&polygon_geojson=1&addressdetails=1&limit=10)
on 21 August 2026 under the ODbL. A coordinate must first fall inside a local relevance
envelope, then Nominatim must return an exact accepted Ahmedabad
or Amdavad value in the structured `city` or `municipality` field, with Gujarat as the
state. A district, county, postcode, free-form display name, or nearby place name is not
enough. If geocoding fails or the structured result is not exact, routing fails closed.
The local rectangle is 72.4200568–72.7400568 E and 22.8615374–23.1815374 N. It is only a
relevance guard: the envelope and structured result are not an AMC boundary or
road-ownership claim, and the wider AUDA planning area and nearby urban bodies are not
treated as Ahmedabad.

No current reusable AMC polygon was found during the 21 August 2026 review. AMC's public
[`AMC_Boundary` ArcGIS layer](http://gis.ahmedabadcity.gov.in/arcgis/rest/services/Our_Ahmedabad/MapServer/10)
exposed an older boundary of about 440.5 km², while an
[AMC publication](https://ahmedabadcity.gov.in/ViewFile/ViewFile?TYPE=FileRepository%2C2204)
describes about 481 km² and merged Bopal-Ghuma, Chiloda, and Kathwada areas. The GIS service
publishes no explicit reuse licence; its HTTPS certificate had expired on 10 July 2024 and
was still invalid during review, although the HTTP service responded. The project therefore
does not redistribute or claim that geometry.

An accepted match is offered AMC's official
[CCRS online complaint flow](https://www.amccrs.com/AMCPortal/View/ComplaintRegistration.aspx?m=Online).
The current citizen Android package is
[`com.amplvb.ccrs`](https://play.google.com/store/apps/details?id=com.amplvb.ccrs).
AMC's [channel instructions](https://www.amccrs.com/AMCPortal/View/ComplainRegistrationMobile.aspx)
also publish WhatsApp **+91 75678 55303**, helpline **155303**, and
`ccrs@ahmedabadcity.gov.in`. The user must finish the complaint in CCRS; contract matching
is disabled.

## Karnataka road-contract data

**Source: KPPP, the Karnataka Public Procurement Portal, Government of Karnataka.**
<https://kppp.karnataka.gov.in>

This is the state's own e-procurement portal. The app uses the same awarded-tender
search that the portal's public website uses, which needs no login, no API key and no
registration. Reproduce it:

```bash
curl -s -X POST "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service/works/search-eproc-tenders?page=0&size=1000&order-by-tender-publish=true" \
  -H "content-type: application/json" \
  -H "Referer: https://kppp.karnataka.gov.in/" \
  -H "Post: CONTRACTOR-EPROC-CONTRACTOR" \
  -d '{"category":"WORKS","status":"AWARDED"}' -D - -o page0.json | grep -i x-total-count
```

That header reports the total the portal holds: **98,009 awarded works** at the time of
writing. `tools/pull-kppp.py` walks those pages and keeps **42,283 road-related rows** by
matching the work title. That is the full source snapshot, not the file downloaded by the
app. The optional tender pack contains the **13,577** rows that are indexed to a supported
municipal body and can actually enter its matcher.

**Verification done on 19 Aug 2026:** of the 1,000 rows on the portal's first page,
**341 appear in the full 42,283-row source snapshot, byte-identical on title, publication
date and location**. This check predates the supported-body reduction and is not a claim
that all 341 appear in the current 13,577-row downloadable pack. The rest of that page is
non-road work the title filter deliberately drops.

Fields taken: `tenderNumber`, `description` (the work title), `locationName`,
`publishedDate`. Nothing is edited beyond truncating long titles.

## Contractor names

**Source: the public-domain snapshot at
<https://bengaluru-road-contracts.pages.dev>, whose own source is KPPP.**

The portal's *search* results do not include the winning bidder; only the per-tender full
view does. Contractor names therefore come from that pre-existing Bengaluru snapshot.
The full 42,283-row source contains **1,124** named bidders; the current downloadable pack
retains **1,121** of them. Its other **12,456** rows have no bidder name. Outside Bengaluru
the complaint names the tender and states plainly that no winning bidder is recorded. It
never guesses a company name.

## Which officer receives the complaint (182 local bodies)

**Sources: the district NIC sites of the Government of Karnataka, and the bodies' own
sites on the Municipal Reforms Cell domain.**
Pattern: `https://<district>.nic.in/en/public-utility-category/municipality/` and
`https://<name>city.mrc.gov.in`.

Every entry in [`data/karnataka-bodies.json`](../data/karnataka-bodies.json), which is
the source for the Karnataka routing/contact pack, carries a
`source` field naming the page its address was read from. The 18 city corporations were
each checked twice, by separate passes, because they carry the most traffic. A body whose
address could not be found on a government page is **not** in the file, and the app
refuses to name a recipient for it rather than guessing.

## Which body owns the road

**Source: KGIS, run by KSRSAC, the Karnataka State Remote Sensing Applications Centre.**

The app asks the state's own GIS which local body's boundary contains the pothole:

```bash
curl -s "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query?geometry=76.6394,12.2958&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=KGISTownName,Town_Type,LGD_TownCode&returnGeometry=false&f=json"
```

Returns `MYSURU`, type `CC`, LGD code `252045`. That layer holds exactly 319 polygons,
matching the state's 319 urban local bodies. Rural points fall through to the gram
panchayat layer.

The officer directory is keyed on **LGD_TownCode**, the Local Government Directory code
issued by the Ministry of Panchayati Raj, so a body is identified by a national
identifier rather than by matching a place name.

## Street address

**Source: OpenStreetMap, via Nominatim.** It turns coordinates into a street name and
pincode for the complaint. In Maharashtra, Kolkata, Delhi, Chennai, and Hyderabad, address
fields are display-only routing clues; only a verified cached polygon selects coverage.
Ahmedabad is the narrow exception described above: it requires an exact structured `city`
or `municipality` value after a local envelope check and makes no boundary claim. Free-form
geocoder text is never used to expand any polygon route. The coordinates themselves
come from the phone's GPS and are printed in the complaint alongside a map link, so the
location can be checked independently.

## What is generated or inferred

The pothole verdict, its size and the one-line description are produced by an AI vision
model looking at the photograph, and the app says so. They are a judgement about a
photograph, not a record, which is why the photograph is always attached: the officer can
disagree by looking.

A probable contract match is also a judgment, not a procurement record. For an eligible
Karnataka route only, the app first builds a deterministic local shortlist from address
words and contracts indexed to the same local body, then asks an AI model whether one
candidate clearly covers that road or locality. A confidence threshold can reject weak
matches, but it cannot turn an accepted match into proof. Contract matching is disabled
for every route outside eligible Karnataka coverage.

For an eligible Karnataka match, warranty status is **inferred** from how recently the
tender was published because award records carry no defect liability period. The complaint
states it as a possibility and asks the officer to verify against the tender documents.
That wording is deliberate and should not be strengthened.

## Known limits

- MMR coverage targets the official notified extent, but its downloaded OpenStreetMap outer
  outline is approximate, differs from MMRDA's published area by about 1%, and does not
  subtract the Scheduled Areas excluded by the 2019 notification. It must not be used as
  a legal boundary. Eleven available civic polygons can select a body; eight bodies,
  rural points, and ambiguities fall back to Aaple Sarkar. A suggestion does not establish
  road ownership.
- PMC coverage uses a verified copy of PMC's official GIS boundary and deliberately excludes
  PCMC. A later PMC boundary change requires a reviewed state-pack update.
- KMC coverage uses a verified, validity-repaired copy of the official West Bengal UDMA
  municipal feature. It excludes Howrah, Bidhannagar/Salt Lake, and New Town. A later KMC
  boundary change requires a reviewed state-pack update.
- Delhi coverage is the full NCT outline only and excludes the wider NCR. Every accepted
  point uses a cross-agency grievance handoff because the outline cannot establish whether
  PWD, MCD, NDMC, Cantonment, DDA, NHAI, or another agency maintains the road.
- Chennai coverage is GCC only. Its ODbL polygon was checked against, but is not copied
  from, GCC's official 2025 layer; later boundary changes require a reviewed pack update.
- Hyderabad coverage is a conservative ODbL core outline and is deliberately partial. It
  offers shared My Cure without assigning a point to one of the three corporations created
  in 2026. The complete official Secunderabad Cantonment layer extent is conservatively
  refused, so some neighbouring civic points are also excluded.
- Ahmedabad depends on Nominatim and an exact structured city-or-municipality match inside
  a local envelope. It is not a municipal boundary and does not cover the wider AUDA area.
- There is no automatic filing, status sync, or cross-user report database for any route.
  Evidence remains local until the citizen deliberately opens an external handoff.
- Maharashtra contract matching is disabled because no authoritative road-linked feed is
  integrated for the MMR or PMC.
- Kolkata contract matching is disabled for the same reason; a KMC boundary match does not
  establish who owns or maintains a road.
- Delhi, Chennai, Hyderabad, and Ahmedabad contract matching is disabled; containment or
  a structured place match is neither ownership nor a contract match.
- 137 of Karnataka's 319 local bodies have no address in the file, because their district
  pages publish none. Those reports refuse to route.
- The full 42,283-row source has 41,159 records without a winning bidder. The downloadable
  13,577-row pack has 12,456 without one and 1,121 with one.
- Of the 42,283 source rows, 18,972 are municipal (DMA or legacy BBMP) records. The pack
  retains the 13,577 indexed to a supported body that can enter the app's contract
  shortlist. The remaining 5,395 municipal rows are unresolved or belong to bodies without
  a published address; the 23,311 non-municipal rows belong to agencies such as PWD,
  panchayats, and irrigation departments and are not candidates for a municipal complaint.
- The road-work filter matches on title keywords, so the candidate pool is inclusive by
  design; the confidence gate on the match is what keeps weak candidates out of a
  complaint.
- Contracts are a snapshot. Re-run `tools/pull-kppp.py` to refresh.
- The public Nominatim endpoint is cached on an approximately 11 m grid and serialized
  below one request per second. National or municipal-scale deployment must switch the
  endpoint to a policy-compliant managed or self-hosted service. MMR, PMC, KMC, Delhi,
  Chennai, and Hyderabad polygon routing remains local and does not depend on a geocoder
  response; Ahmedabad intentionally does.
