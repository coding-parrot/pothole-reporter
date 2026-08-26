# Where the app's data and judgments come from

This document records provenance, transformations, and limits. The app combines a
user's camera and phone location with government reference data, OpenStreetMap data,
local matching rules, and AI-generated judgments. A source record is not proof that a
particular defect, authority, or contract match is correct. The user must review the
photo, routing, and any probable contract match before sending a complaint.

## README India map

The README coverage illustration uses DataMeet India community's
[`india-composite.geojson`](https://github.com/datameet/maps/blob/5ed214bf77788f99066e3542cccd4a52cb042896/Country/india-composite.geojson)
at commit `5ed214bf77788f99066e3542cccd4a52cb042896`. The source's SHA-256 is
`5e44c39b18aa8fe57267d8018fa4ad4a10eaa3aa4cb7cb7382a1813ef8eb8c53`.
Its dataset note assigns **CC0** and says the composite includes disputed territories in
accordance with the official Survey of India boundary. The project simplifies that
geometry to 0.025 degrees for a 320-pixel SVG while retaining the mainland territorial
outline, Lakshadweep, and the Andaman and Nicobar Islands.

The Government of India's
[Geospatial Guidelines](https://onlinemaps.surveyofindia.gov.in/GeospatialGuidelines.aspx)
make Survey of India boundaries the standard for political maps and permit compliant
publication. The README does not copy Survey of India's political-map artwork, logo, or
emblem; its [website copyright policy](https://surveyofindia.gov.in/pages/copyright-policy)
separately requires permission to reproduce website material. Coverage colour, city dots,
and annotations are Pothole Reporter content and imply no government endorsement. This is
a README illustration only; runtime routing continues to use the independently versioned,
checksum-pinned packs documented below.

## Versioned data packs

Large routing, contact, and procurement datasets are not embedded in the APK. Small
manifests pin each resource's version, HTTPS URL, byte length, and SHA-256 checksum. The
v1.35 state-pack manifest contains 42 resources: forty-one routing/contact packs covering
all 28 states and all 8 Union Territories, plus the optional Karnataka tender pack.
Separate v1.36 catalogs contain 33 State/UT National Highway project packs, 36 PMGSY
State/UT road-agreement packs, and 34 official State/UT road-notice packs assembled from
GePNIC and dedicated public-portal adapters. Karnataka, Tamil
Nadu, and Telangana retain separate statewide and more-specific routing packs.
The old unversioned ten-resource manifest remains unchanged for cached v1.25 clients, and
the versioned v1.26 eleven-resource manifest remains unchanged for cached v1.26 clients.
The versioned v1.27 twelve-resource, v1.28 thirteen-resource, and v1.29 thirteen-resource
manifests also remain unchanged for their cached clients. The v1.30 fifteen-resource
manifest and the v1.31 seventeen-resource manifest remain unchanged for their cached clients;
v1.32 reused v1.31 and introduced no catalog. The v1.33 eighteen-resource manifest also
remains unchanged for its cached clients. The v1.34 twenty-two-resource manifest also remains
unchanged. v1.35 reads `pack-manifest-v1.35.json`, preventing a mixed-cache release from
disabling otherwise valid routing. Its Karnataka tender resource uses the
carriageway-scope-filtered v2 adapter.
The Gujarat packs keep the reviewed 48-ward AMC boundary and statewide containment
separate.

The app also ships a National Highway manifest that pins 101 immutable 2° geometry tiles.
It downloads a pack or relevant tile from this project's GitHub Pages site, verifies the complete
response against the manifest before parsing it, and caches only verified bytes on the
device. Missing, malformed, truncated, or checksum-mismatched required routing/contact
data makes the affected route fail closed. A geocoder place name is not a fallback for a
polygon route. Failure of any optional project/agreement/notice pack does not block the report—it
continues without contract context. A verified
pack can be read locally after its first
successful download. On a subsequent pack use, caches past their unused limits are pruned
automatically, and **Delete all app data** removes the entire cache immediately.

The URL names the selected state or a 2° highway tile, so GitHub Pages can receive the
device's IP address, standard connection metadata, and that coarse location indication.
No report, road photo, or exact coordinates are included. Hosted packs keep the core APK
small. The three catalog manifests add about 100 KB to a future build; the remote packs do
not enter the APK, and the already-submitted Play closed-test binary is unchanged.

### Contract and procurement-notice semantics

The 26 August 2026 National Highway scan read 3,258 MoRTH and 529 NHIDCL rows. Strict
lifecycle and road-scope rules normalized 1,950 current/open records (1,939 MoRTH plus 11
NHIDCL); 91 had no mapped NH/NE reference, leaving 1,859 candidates in 33 packs
(1,805,723 bytes).

The official PMGSY scan read 211,007 rows and retained 17,717 recent records whose source
status says **In Progress**. They are published in 36 State/UT packs (3,075,718 bytes);
CH, DH, DL, GA, and LD currently have zero retained records. An agreement number/date can
verify that the source reports an agreement, but this layer never claims an award,
contractor, exact segment, warranty, or DLP.

The GePNIC crawl completed 30 official feeds across 29 jurisdictions with zero source
failures. Dedicated, fail-closed adapters also read the public Bihar eProc2, Chhattisgarh
CHiPS, Gujarat nProcure, Telangana eProcurement, and Lakshadweep notice indexes. Every
published notice retains its exact official ID/reference, work title, organisation where
published, available dates, and source link. Missing fields stay null; no award, contractor,
road segment, warranty, or DLP is inferred. Andhra Pradesh remains explicitly blocked
because its complete table requires undocumented client-encrypted state and it exposes no
stable record link; KPPP remains a separate Karnataka supplement.
Together these 35 public feeds scanned 69,754 rows, rejected 64,358 through strict
scope/current-record checks, and published 5,396 notices in 34 jurisdiction packs
(4,296,746 bytes).

Runtime matching attempts National Highway, Karnataka KPPP, PMGSY, and official State/UT
notice candidates
where the required same-jurisdiction road/location evidence is present. This improves
nationwide candidate coverage but does not guarantee every public tender or every road.
An open procurement notice is not an award or active maintenance contract. Every result
remains a candidate unless authoritative road-segment, work-scope, award, contractor, and
DLP evidence is separately present. Publication date never establishes warranty.

## National Highway geometry and handoff

- **Source:** OpenStreetMap India extract distributed by
  [Geofabrik](https://download.geofabrik.de/asia/india.html), ODbL 1.0.
- **Pinned extract:** 20 August 2026,
  `https://download.geofabrik.de/asia/india-260820.osm.pbf`, MD5
  `c5e0a62a1cb00c80d8c5948bf18370d7`.
- **Filter:** operational drivable ways carrying an `NH*` or `NE*` reference, or the
  `IN:NH` network tag. Proposed, construction-only, service, residential, path, and
  track classes are excluded.
- **Output:** 142,656 accepted source features, 680 distinct mapped references, and
  101 content-addressed 2° tiles. Geometry is simplified to 2 m and each tile is
  byte-length and SHA-256 verified before parsing.
- **Runtime rule:** check the relevant highway tile before any municipal route; require
  GPS accuracy of 30 m or better; refuse different nearby references, moving-direction
  conflicts, missing data, and invalid data.
- **Handoff:** [Rajmargyatra](https://play.google.com/store/apps/details?id=com.nhai.rajmargyatra),
  [CPGRAMS](https://pgportal.gov.in/), and helpline `1033`.

The geometry describes mapped NH/NE carriageways, not a legal inventory or ownership
register. It does not infer whether NHAI, NHIDCL, BRO, or a State PWD maintains a matched
stretch. The user must verify the maintaining agency in the official service. Rebuild and
verification logic is in `tools/build-national-highways.py`; the complete source receipt
is `data/national-highways-source.json`.

## Maharashtra boundaries and complaint handoffs

### Statewide fallback

Every point inside [OpenStreetMap relation 1950884](https://www.openstreetmap.org/relation/1950884)
is covered by a neutral [Aaple Sarkar](https://grievances.maharashtra.gov.in/en)
handoff after the National Highway, MMR, and PMC checks. The ODbL MultiPolygon was
retrieved on 23 August 2026 at seven-decimal precision; the runtime pins geometry SHA-256
`1f5555fede30d19d58ffafabb7d38c8cba0af7b27f7c7129d10480351a0304ce`.
The official grievance-management resolution says the service operates across all 36
districts and supports evidence, tracking, and district-level routing. The app still does
not identify whether a municipality, PWD, panchayat, MSRDC, NHAI, or another body owns the
road. The citizen must select and verify the department in Aaple Sarkar; MahaULB is offered
as an alternate for urban areas. A GPS circle touching the state or a more specific local
boundary fails closed.

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
the nearest city. Maharashtra's official
[grievance-management resolution](https://grievances.maharashtra.gov.in/uploads/files/GR_GrievanceManagement_24082016.pdf)
documents online lodging and tracking, attachments, district-level municipal-corporation
routing, and the nodal-officer process. The app still does not claim that Aaple Sarkar has
identified a particular MMR body or issue owner. The Maharashtra routing pack contains a locally evaluated aid made from OpenStreetMap
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
It selects the current Pune Municipal Corporation route only; Pimpri-Chinchwad Municipal
Corporation (PCMC) and places outside PMC use the statewide Maharashtra fallback. Road damage is
offered PMC Road Mitra. Garbage and manhole reports use
[PMC CARE](https://www.pmccare.in/); PMC's official
[CARE booklet](https://opendata.pmc.gov.in/opendata/PMCReports/PMC-CARE-Booklet.pdf)
describes a single-window civic-grievance framework, trackable tickets, WhatsApp
**+91 96899 00002**, and toll-free **1800 103 0222**. The current Android package is
[`in.gov.pmc.pmccare`](https://play.google.com/store/apps/details?id=in.gov.pmc.pmccare).
The public material does not expose every category name before login, so the app calls
this a general civic handoff rather than guaranteed category acceptance.

### What a handoff proves

Pothole Reporter prepares evidence; it does not log in, bypass OTP, call a complaint-write
API, press Send, or read complaint status. Opening an app or portal records only “handoff
opened.” Share, WhatsApp, Call, and opening an email draft also do not prove submission.
Where the report uses an official app or portal, it can be marked submitted only after the
user records the grievance/reference ID returned by that service. The user must review and
complete every complaint in the external channel.

For Greater Mumbai, road damage can use
[Pothole QuickFix](https://play.google.com/store/apps/details?id=com.bmc.potholequickfix),
which identifies BMC as its developer. Garbage and manhole reports use BMC's
[Management and Redressal of Grievance (MyBMC MARG)](https://marg.mcgm.gov.in/MARG/welcomePage.html)
portal and its linked Android package
[`com.esri.ugms_bmc`](https://play.google.com/store/apps/details?id=com.esri.ugms_bmc).
BMC's [main website](https://www.mcgm.gov.in/irj/portal/anonymous?guest_user=english)
lists MyBMC MARG as a civic service; the public pre-login portal does not enumerate every
category, so the app does not promise that a specific category will be accepted. BMC also
publishes **1916** as its civic helpline. The app opens those channels only after the user
chooses the action. BMC's website policy at
`https://portal.mcgm.gov.in/irj/portal/anonymous/privacy?guest_user=english` is why the app
does not frame BMC pages, copy BMC marks, or claim affiliation.

Maharashtra has no authoritative statewide road-linked award and defect-liability feed.
The optional GePNIC catalog can show a same-State open-notice candidate, but it never
guesses a contractor, warranty, road owner, or awarded responsibility from that notice.

## West Bengal statewide and Kolkata complaint handoffs

### Statewide fallback

Every point confidently inside
[OpenStreetMap relation 1960177](https://www.openstreetmap.org/relation/1960177) is
covered after the National Highway and exact KMC checks. The ODbL MultiPolygon was
retrieved on 24 August 2026 at seven-decimal precision; the runtime pins geometry
SHA-256 `aa4ab13c3064be2e168889f6eb02e87c59e01bc709d36b66bece534dfea23015`.
It is a routing aid, not a legal boundary or ownership record. A GPS circle touching the
state or KMC boundary fails closed rather than guessing.

Outside the exact KMC polygon, the app offers the Government of West Bengal's general
[Public Grievance Redressal System](https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx),
with [CMO Grievance](https://cmo.wb.gov.in/landing/raise-grievance) as an alternate.
PGRS is offered for road damage, garbage, and open or damaged manholes as a neutral
handoff. The user must select and verify the district or department and complete the
complaint externally. The app does not infer a municipality, PWD, panchayat, road owner,
contractor, or guaranteed category acceptance, and it does not submit automatically.

### Kolkata Municipal Corporation

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

The KMC-specific route is limited to current Kolkata Municipal Corporation limits.
Howrah, Bidhannagar/Salt Lake, New Town, and every other West Bengal point outside KMC
use the statewide PGRS route instead. A place name from Nominatim is contextual only and
cannot select KMC or statewide containment.

The primary handoff is [KMC Grievance 2.0](https://kmc.wb.gov.in/citizen/language-selection),
which requires the user to continue through KMC's external login and complaint flow. The
[official KMC Android app](https://play.google.com/store/apps/details?id=com.kmc.app) is an
alternate. KMC also publishes WhatsApp at **+91 83359 88888** and the civic helpline
**1800 345 3375** on its [contact page](https://www.kmcgov.in/KMCPortal/jsp/KmcContact.jsp).
Opening any channel is not submission. The user must complete the complaint externally
and enter the KMC grievance/reference ID before marking the local report submitted.

West Bengal has no authoritative road-linked statewide or KMC award, maintenance, or
defect-liability feed. The optional GePNIC catalog can show a same-State open-notice
candidate, but boundary containment never implies that KMC or another body owns the road.

## Punjab statewide complaint handoff

Every point confidently inside
[OpenStreetMap relation 1942686](https://www.openstreetmap.org/relation/1942686) is eligible
for a neutral [Connect Punjab](https://connect.punjab.gov.in/) handoff after the National
Highway check. The ODbL polygon was retrieved on 24 August 2026 at seven-decimal precision;
the runtime pins geometry SHA-256
`e113eb774f4f353d3c7a9c98830f4b665f9bd4d166ed3b84e90855bdf38f5782`.
[Punjab mSeva](https://mseva.lgpunjab.gov.in/) is offered as an urban-area alternate and
Connect Punjab publishes helpline **1100**. Chandigarh Union Territory is outside the
Punjab boundary. A GPS accuracy circle touching the state edge fails closed.

State containment does not identify a municipality, PWD, panchayat, NHAI, contractor,
road owner, or guaranteed complaint category. The user must select and verify the
responsible department or local body, complete the complaint externally, and retain the
official reference. Only optional open-notice candidates are available; awarded
responsibility is not inferred.

## Tamil Nadu statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 96905](https://www.openstreetmap.org/relation/96905) is eligible
for a neutral [Mudhalvarin Mugavari](https://cmhelpline.tnega.org/portal/en/home) handoff
after the National Highway and exact Greater Chennai Corporation checks. The ODbL state
MultiPolygon was retrieved on 24 August 2026 at seven-decimal precision; the runtime pins
geometry SHA-256
`b3034527326b1120366adaf4b7c3df4bd0b8c7aab4d82b28e3dde189b39c313e`.
Puducherry Union Territory, including Karaikal, is outside this Tamil Nadu boundary. A GPS
accuracy circle touching the state edge or an excluded enclave fails closed.

The primary service's official citizen Android package is
[`org.tnega.cmhelpline.citizen`](https://play.google.com/store/apps/details?id=org.tnega.cmhelpline.citizen),
and its published helpline is **1100**. The statewide route deliberately does not guess a
secondary municipal portal. Neither the state outline nor the grievance handoff establishes the
responsible department, local body, road owner, complaint category, contractor, or
submission. The user must verify those details and complete the complaint externally.
There is no complaint-write API integration or automatic filing. A same-State public
notice may appear as an unawarded candidate; it is not responsibility evidence.

## Andhra Pradesh statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 2022095](https://www.openstreetmap.org/relation/2022095) is eligible
for a neutral [Andhra Pradesh PGRS](https://pgrs.ap.gov.in/) handoff after the National
Highway check. The ODbL state MultiPolygon was retrieved on 24 August 2026 at seven-decimal
precision; the runtime pins geometry SHA-256
`4e36d9c16fda044dceab7a5b08955cb19046bb1bddd052b7671a8311e90cd71c`.
Yanam is part of Puducherry Union Territory and is explicitly outside this Andhra Pradesh
route. A GPS accuracy circle touching the state edge, Yanam, or a neighbouring state fails
closed.

PGRS is the primary neutral handoff for road damage, garbage, and open or damaged manholes.
[AP CDMA/Puramithra](https://cdma.ap.gov.in/services/grievances/) is offered as an urban-area
alternate, and the published grievance helpline is **1902**. The user must select and verify
the district, department, local body, issue category, and road owner, then complete the
complaint externally. The app does not call a complaint-write API, file automatically,
or infer ownership. The public Andhra Pradesh tender list was verified, but a complete
machine-readable pull requires undocumented client-encrypted state and exposes no stable
record link. The app therefore publishes no AP state-notice pack rather than bypassing the
portal or fabricating fields; National Highway and PMGSY candidates remain available.

The outline was cross-checked against the Government of Andhra Pradesh
[APSAC administrative-boundary services](https://apsac.ap.gov.in/?page_id=1075) and the
[Survey of India state-map directory](https://surveyofindia.gov.in/pages/state-maps).
Those official sources are validation evidence only: APSAC publishes no explicit
redistribution licence for the boundary, while the Survey of India map restricts
reproduction without permission. The project therefore redistributes the independently
licensed OpenStreetMap geometry under the ODbL, not either official geometry.

## Telangana statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 3250963](https://www.openstreetmap.org/relation/3250963) is eligible
for a neutral [Telangana Prajavani](https://prajavani.cgg.gov.in/) handoff after the National
Highway and exact Hyderabad CURE checks. The ODbL state polygon was retrieved on 24 August
2026 at seven-decimal precision; the runtime pins geometry SHA-256
`77183815e4b698ec1e823f4a94a6f213d1d827ea35de8fec8c0ab3b6a9d15175`.
A GPS accuracy circle touching the state edge fails closed rather than inheriting a route
from a place name.

The Government of Telangana's [Prajavani portal](https://prajavani.cgg.gov.in/) is the
neutral statewide handoff for road damage, garbage, and open or damaged manholes. The
[Centre for Good Governance project record](https://www.cgg.gov.in/it_project/prajavani-praja-bhavan-effective-grievance-redressal-system-2/)
describes state-level rollout, direct citizen submission, and routing to district or
department officers by category and location. The portal does not provide a stable public
prefill or third-party complaint-write API, so the user must select and verify the district,
department, local body, category, and road owner, complete the complaint externally, and
retain the official acknowledgement. Pothole Reporter does not log in, bypass OTP, submit,
or read complaint status. The discontinued `cpgrams.ts.nic.in` portal is not used.

[Citizen Buddy Telangana](https://play.google.com/store/apps/details?id=vmax.com.citizenbuddy)
is offered only as an urban alternate for municipalities and corporations outside Hyderabad;
it is not presented as a rural or statewide channel. Inside Hyderabad, a successful official
CURE and Cantonment check keeps the more specific
[My Cure](https://play.google.com/store/apps/details?id=cgg.gov.ghmc) route. A CURE-service
failure or an accuracy envelope intersecting Secunderabad Cantonment prevents My Cure but
can still fall back to neutral Prajavani when the checksum-pinned state polygon contains the
complete accuracy circle. That fallback does not claim that a Telangana department owns a
Cantonment road.

The ODbL outline was cross-checked against TGRAC's official
[`State Boundary` layer 29](https://tgrac.telangana.gov.in/arcgis/rest/services/AdministrativeInfoSystem_Folder/Administrative_Information_System/MapServer/29).
TGRAC publishes no explicit redistribution licence for that layer, so it is validation
evidence only; the project redistributes the independently licensed OpenStreetMap geometry.
The dedicated Telangana adapter reads the anonymous official live-tender table and retains
strict road-surface notices with their ID/reference, exact work title, department,
published/bid-start/closing dates, and official source. Bid opening, contractor, segment,
award, warranty, and DLP remain unknown when the public listing omits them.

## Karnataka statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 2019939](https://www.openstreetmap.org/relation/2019939) is eligible
for a neutral [Janaspandana iPGRS](https://ipgrs.karnataka.gov.in/) handoff after the
National Highway and verified Karnataka urban-body checks. The ODbL state MultiPolygon was
retrieved on 25 August 2026 at seven-decimal precision; the runtime pins geometry SHA-256
`9d7fe3f01a80cb41712c09139efcd43e0e11a644849d5f3bffe125cc0bc1c5ad`.
A GPS accuracy circle touching the state edge fails closed rather than inheriting a route
from a geocoder label.

Janaspandana is the Government of Karnataka's Integrated Public Grievance Redressal System.
Its current official page says 41 departments and 270 line departments are onboarded and
publishes helpline **1902**. [Janahitha](https://www.mrc.gov.in/janahita/login) is offered
as an urban-local-body alternate; Karnataka district NIC pages identify it as a citizen
service, but the user must select and verify the correct body and category. Existing
verified municipal-recipient and Bengaluru civic routes remain more specific and take
precedence over the statewide fallback.

Neither state containment, a KGIS result, nor either grievance service establishes the
department, local body, road owner, contractor, category acceptance, or successful
submission. Pothole Reporter does not call a complaint-write API, log in, bypass OTP, or
submit automatically. The neutral statewide route does not enable Karnataka contract
matching; that remains limited to an otherwise eligible, verified municipal route.

## Kerala statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 2018151](https://www.openstreetmap.org/relation/2018151) is eligible
for a neutral [Kerala CMO Public Grievance Redressal](https://cmo.kerala.gov.in/) handoff
after the National Highway check. The ODbL state MultiPolygon was retrieved on 25 August
2026 at seven-decimal precision; the runtime pins geometry SHA-256
`51e226750b1d6c08a5030e6074e2641282e01c328f46d8aee741de664bef705c`.
Mahe is part of Puducherry Union Territory and is outside the Kerala boundary. A GPS
accuracy circle touching Mahe, the state edge, or a neighbouring state fails closed.

The Government of Kerala portal accepts online grievances and provides docket-based status.
Its toll-free **1076** line provides help and status information; the official FAQ says a
complaint cannot be lodged by phone. The official Local Self Government Department's
[K-SMART](https://ksmart.lsgkerala.gov.in/) service is offered as an alternate for
local-body issues. The user must select and verify the department, local body, issue
category, and road owner and complete the complaint externally. The app does not claim a
public complaint-write API, automatic filing, or ownership inference. A same-State open
procurement notice can appear only as an unawarded candidate.
The seven Kerala entries retained in the immutable top-50 pack are compatibility data only;
current routing uses the exact state boundary instead of their old city-name envelopes.

## Uttar Pradesh statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1942587](https://www.openstreetmap.org/relation/1942587) is eligible
for a neutral [UP Jansunwai–Samadhan](https://jansunwai.up.nic.in/?language=en_US) handoff
after the National Highway and exact Delhi NCT checks. The ODbL state MultiPolygon was
retrieved on 25 August 2026 at seven-decimal precision; the runtime pins geometry SHA-256
`2dbb5237cab5eb029f517c1d79451663c1fc49affe0e0789b11f0565180db015`.
Delhi NCT is outside the Uttar Pradesh boundary and retains its own route. A GPS accuracy
circle touching Delhi, the state edge, or a neighbouring state fails closed.

Jansunwai is a Government of Uttar Pradesh grievance service. Its official citizen Android
package is
[`in.nic.up.jansunwai.upjansunwai`](https://play.google.com/store/apps/details?id=in.nic.up.jansunwai.upjansunwai),
and the published Chief Minister Helpline is **1076**. The public complaint flow is
interactive and can require mobile verification and CAPTCHA. The user must select and
verify the district, department, local body, issue category, and road owner, then complete
the grievance externally. State containment does not identify any of those details, and
Pothole Reporter does not call a complaint-write API, log in, bypass verification, file
automatically or read status. A same-State open procurement notice can appear only as an
unawarded candidate. The seven Uttar Pradesh entries
retained in the immutable top-50 pack are compatibility data only; current routing uses the
exact state boundary.

## Chhattisgarh statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1972004](https://www.openstreetmap.org/relation/1972004) is eligible
for a neutral [Chhattisgarh CM Helpline](https://cmhelpline.cg.gov.in/Home/VerifyOTPBeforeOnlineComplaint)
handoff after the National Highway check. The ODbL state polygon was retrieved on
25 August 2026 at seven-decimal precision; the runtime pins geometry SHA-256
`827e89a598571ade84db77390bca5daf98c9f67fbae716b17193f4ccdc2876eb`.
A GPS accuracy circle touching the state edge or a neighbouring state fails closed.

The statewide service publishes helpline **1076**. [NIDAAN 1100](https://crm.nidaan.cg.gov.in/)
is offered only as an urban civic-issue alternate, not as a rural or statewide substitute.
The user must select and verify the district, department, local body, category, and road
owner and complete the grievance externally. Neither containment nor either service proves
responsibility, ownership, category acceptance, submission, or a contract. Pothole Reporter
does not call a complaint-write API, log in, bypass OTP, file automatically, or read status.
The dedicated Chhattisgarh adapter reads the anonymous CHiPS open-tender table and public
detail pages, preserving exact NIT references, opening dates, organisation hierarchy and
official POST locator. It asserts no contractor, award, segment, warranty, or DLP. The
Raipur and Durg–Bhilai entries retained in the immutable
top-50 pack are compatibility data only; current routing uses the exact state boundary.

## Rajasthan statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1942920](https://www.openstreetmap.org/relation/1942920) is eligible
for a neutral [Rajasthan Sampark 2.0](https://sampark.rajasthan.gov.in/grievanceForm)
handoff after the National Highway check. The ODbL state MultiPolygon was retrieved on
25 August 2026 at seven-decimal precision; the runtime pins geometry SHA-256
`dcde670675d0fc50e292c6b306b1f80d9d68a1323250c29d6eddc97992491a36`.
A GPS accuracy circle touching the state edge, a neighbouring state, or the international
border fails closed.

The official Sampark portal publishes toll-free helpline **181**, and its current Android
package is
[`com.rajsampark.versiontwo`](https://play.google.com/store/apps/details?id=com.rajsampark.versiontwo).
The user must select and verify the district, department, local body, category, and road
owner and complete the grievance externally. State containment does not prove any of
those details. Pothole Reporter does not call a complaint-write API, log in, bypass OTP,
file automatically or read status. A same-State open procurement notice can appear only
as an unawarded candidate. Jaipur and Jodhpur remain
in the immutable top-50 pack only for saved-report compatibility; current routing uses
the exact state boundary.

## Goa statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 11251493](https://www.openstreetmap.org/relation/11251493) is eligible
for neutral [CM Helpline Goa](https://cmhelpline.dpg.goa.gov.in/) after the National Highway
check. The ODbL MultiPolygon was retrieved on 25 August 2026 at seven-decimal precision; the
runtime pins geometry SHA-256
`f4c47a79a3671d333d47f66a597d66b6295a78b1cd7cd3cba7bc2db472190e4f`.
A GPS circle touching the coast, state edge, or a neighbouring state fails closed. Relation
`1997192`, found in older references, is obsolete and is not used.

The Directorate of Public Grievances says its jurisdiction covers the entire state. Its
current portal explicitly accepts infrastructure/road and health/sanitation grievances,
offers document uploads and tracking, and publishes the 24/7 toll-free helpline **1905**.
The official citizen Android package is
[`in.gov.dpg.cmhelpline`](https://play.google.com/store/apps/details?id=in.gov.dpg.cmhelpline).
The user must complete the mobile-OTP flow and verify the department, local body, category,
and road owner. There is no documented public complaint-write API integration, automatic
filing or ownership inference. A same-State open procurement notice can appear only as an
unawarded candidate.

## Madhya Pradesh statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1950071](https://www.openstreetmap.org/relation/1950071) is eligible
for the neutral [Madhya Pradesh CM Helpline](https://www.cmhelpline.mp.gov.in/Public/VerifyOTPBeforeOnlineComplaint.aspx)
after the National Highway check. The ODbL MultiPolygon was retrieved on 25 August 2026 at
seven-decimal precision; the runtime pins geometry SHA-256
`24f0c93ed8bd40c4c6b4e1f650c3b9870b1e65ccd5d7b00ea0193a8a5aedc357`.
A GPS circle touching the state edge or a neighbouring state fails closed.

The official portal requires a mobile number and OTP and publishes complaint registration
and status tracking. Its citizen Android package is
[`com.magnum.helpline`](https://play.google.com/store/apps/details?id=com.magnum.helpline),
and the service uses helpline **181**. The user must verify the district, department, local
body, category, and road owner and complete the grievance externally. There is no documented
public complaint-write API integration, automatic filing, ownership inference, or contract
matching. Indore, Bhopal, Jabalpur, and Gwalior remain in the immutable top-50 pack only for
saved-report compatibility; current routing uses the exact state boundary.

## Bihar statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1958982](https://www.openstreetmap.org/relation/1958982) is eligible
for neutral [Bihar Lok Shikayat](https://lokshikayat.bihar.gov.in/Default.aspx) after the
National Highway check. The ODbL MultiPolygon was retrieved on 25 August 2026 at
seven-decimal precision; the runtime pins geometry SHA-256
`3d846e20cfee28a656d6dd808c4dad37a4f1c95852f9f292b0acefde708f4b24`.
A GPS circle touching Nepal, the state edge, or a neighbouring state fails closed.

The official portal supports new grievances, appeals, status lookup, and publishes toll-free
helpline **1800 345 6284**. Its official Android package is
[`com.bpsms.jansamadhan`](https://play.google.com/store/apps/details?id=com.bpsms.jansamadhan).
Lok Shikayat applies to eligible state schemes, programmes, services, and public authorities;
state containment does not guarantee that a civic category is accepted. The user must select
and verify the public authority, local body, category, and road owner. There is no documented
public complaint-write API integration, automatic filing, ownership inference, or contract
matching. Patna remains in the immutable top-50 pack only for saved-report compatibility;
current routing uses the exact state boundary.

## Odisha statewide complaint handoff

Every point whose complete GPS-accuracy circle is confidently inside
[OpenStreetMap relation 1984022](https://www.openstreetmap.org/relation/1984022) is eligible
for neutral [Odisha Jana Sunani](https://janasunani.odisha.gov.in/grievance-details) after
the National Highway check. The ODbL MultiPolygon was retrieved on 25 August 2026 at
seven-decimal precision; the runtime pins geometry SHA-256
`af0fe4941b6cdd2abe5dc5717db8875bec6b68a2d6671002d2afc9c7d37d5179`.
A GPS circle touching the coast, state edge, or a neighbouring state fails closed.

The Government of Odisha portal accepts grievances through web, mobile app, WhatsApp,
email, letter, and in-person channels and routes them to selected districts, departments,
or offices. The official Android package is
[`com.sociomatic.janasunani`](https://play.google.com/store/apps/details?id=com.sociomatic.janasunani),
the WhatsApp chatbot is **+91 63709 51930**, and the Government of Odisha publishes Sanjog
helpline **155335**. Even the skip-login form requires mobile OTP and interactive locality
and recipient choices. The user must verify those choices and complete the grievance
externally; there is no documented public complaint-write API integration, automatic filing,
ownership inference. A same-State open procurement notice can appear only as an unawarded
candidate.

## Nationwide completion boundaries and complaint handoffs

Twenty separate checksum-pinned packs complete coordinate-based civic routing for all
28 states and all 8 Union Territories. They add or expand the 13 states and 7 Union
Territories below; the other 16 jurisdictions are documented in their existing sections.
Each ODbL administrative relation was retrieved through OpenStreetMap Nominatim on
26 August 2026 at seven-decimal precision. The reviewed relation ID, source bounding box,
geometry SHA-256, test points, and handoff metadata are pinned in
[`tools/india_jurisdictions.py`](../tools/india_jurisdictions.py). A builder refuses a
different object or changed geometry.

| Jurisdiction | Pinned OSM relation | Conservative official handoff |
| --- | ---: | --- |
| Arunachal Pradesh | [2027346](https://www.openstreetmap.org/relation/2027346) | [CM e-Jan Sunwai](https://cmejansunwai.arunachal.gov.in/) |
| Assam | [2025886](https://www.openstreetmap.org/relation/2025886) | [CPGRAMS](https://pgportal.gov.in/) |
| Gujarat | [1949080](https://www.openstreetmap.org/relation/1949080) | [SWAGAT](https://swagat.gujarat.gov.in/) |
| Haryana | [1942601](https://www.openstreetmap.org/relation/1942601) | [CPGRAMS](https://pgportal.gov.in/) |
| Himachal Pradesh | [364186](https://www.openstreetmap.org/relation/364186) | [eSamadhan](https://esamadhan.nic.in/welcome.aspx) |
| Jharkhand | [1960191](https://www.openstreetmap.org/relation/1960191) | [CPGRAMS](https://pgportal.gov.in/), with [Jharkhand municipal PGMS](https://pgms.dmajharkhand.in/index.aspx) as an alternate |
| Manipur | [2027869](https://www.openstreetmap.org/relation/2027869) | [GovConnect Manipur](https://govconnectmanipur.mn.gov.in/) |
| Meghalaya | [2027521](https://www.openstreetmap.org/relation/2027521) | [CM Connect](https://cmconnect.meghalaya.gov.in/) |
| Mizoram | [2029046](https://www.openstreetmap.org/relation/2029046) | [Mipui Aw](https://mipuiaw.mizoram.gov.in/) |
| Nagaland | [2027973](https://www.openstreetmap.org/relation/2027973) | [CPGRAMS](https://pgportal.gov.in/) |
| Sikkim | [1791324](https://www.openstreetmap.org/relation/1791324) | [CPGRAMS](https://pgportal.gov.in/), with the [Sikkim State Portal](https://www.sikkim.gov.in/) as an alternate |
| Tripura | [2026458](https://www.openstreetmap.org/relation/2026458) | [CM Helpline](https://cmhelpline.tripura.gov.in/) |
| Uttarakhand | [9987086](https://www.openstreetmap.org/relation/9987086) | [CM Helpline](https://cmhelpline.uk.gov.in/) |
| Andaman and Nicobar Islands UT | [2025855](https://www.openstreetmap.org/relation/2025855) | [CPGRAMS](https://pgportal.gov.in/) |
| Chandigarh UT | [1942809](https://www.openstreetmap.org/relation/1942809) | [CPGRAMS](https://pgportal.gov.in/) |
| Dadra and Nagar Haveli and Daman and Diu UT | [1952530](https://www.openstreetmap.org/relation/1952530) | [CPGRAMS](https://pgportal.gov.in/) |
| Jammu and Kashmir UT | [1943188](https://www.openstreetmap.org/relation/1943188) | [JK Samadhan](https://samadhan.jk.gov.in/) |
| Ladakh UT | [5515045](https://www.openstreetmap.org/relation/5515045) | [Ladakh grievance portal](https://grievance.ladakh.gov.in/) |
| Lakshadweep UT | [2027460](https://www.openstreetmap.org/relation/2027460) | [CPGRAMS](https://pgportal.gov.in/) |
| Puducherry UT | [107001](https://www.openstreetmap.org/relation/107001) | [CPGRAMS](https://pgportal.gov.in/) |

National Highway matching runs first. Existing exact municipal, metropolitan, and
authority-specific routes also remain preferred. A fallback is eligible only when the
complete GPS-accuracy circle is inside one pinned state/UT polygon; coast, enclave, and
border ambiguity fails closed. Containment chooses a neutral starting channel only. It
does not identify a municipality or road owner, guarantee that a category will be
accepted, call a complaint-write API, or submit anything. The citizen must choose and
verify the recipient in the official service and retain its reference ID.

## Census top-50 city routes

The selection list is the 50 largest serial-numbered Urban Agglomeration/City entries by
`Persons` in the Office of the Registrar General & Census Commissioner's official
[Census 2011 A-04(I) table](https://censusindia.gov.in/nada/index.php/catalog/42876) and
[workbook](https://censusindia.gov.in/nada/index.php/catalog/42876/download/46544/CLASS_I.xlsx).
Census 2011 is selection metadata, not current jurisdiction geometry. Historical/current
aliases such as Bruhat Bangalore/Bengaluru, Ahmadabad/Ahmedabad,
Allahabad/Prayagraj, and Aurangabad/Chhatrapati Sambhajinagar are retained separately.

Forty-two entries already use a reviewed city, NCT, Karnataka-body, or statewide
Maharashtra/West Bengal/Punjab/Karnataka/Kerala/Tamil Nadu/Andhra Pradesh/Telangana/Uttar Pradesh/Chhattisgarh/Rajasthan/Goa/Madhya Pradesh/Bihar/Odisha route. The other 8 use a checksum-verified national
routing pack. A new route is offered only when all of these agree:

For saved-report compatibility, that immutable pack still physically contains twenty-seven entries
now covered statewide: Coimbatore and Madurai in Tamil Nadu; Visakhapatnam and Vijayawada
in Andhra Pradesh; and Kochi, Kozhikode, Thrissur, Malappuram, Thiruvananthapuram, Kannur,
and Kollam in Kerala; Kanpur, Lucknow, Ghaziabad, Agra, Varanasi, Meerut, and Prayagraj in
Uttar Pradesh; Raipur and Durg–Bhilai in Chhattisgarh; Jaipur and Jodhpur in Rajasthan;
Indore, Bhopal, Jabalpur, and Gwalior in Madhya Pradesh; and Patna in Bihar.
It keeps the v1.25 checksum. Current routing checks the exact state polygons first, so
those entries are not counted among the 8 active structured-city
routes and do not override statewide coverage.

- GPS accuracy is 30 m or better and the point lies strictly inside the route's
  conservative relevance envelope;
- Nominatim returns an exact configured alias in a structured `city` or `municipality`
  field and a matching state alias; and
- the verified pack supplies the reviewed neutral official grievance handoff for that state.

The 8 centres are Surat, Vadodara, Faridabad, Rajkot, Jamshedpur, Srinagar, Dhanbad,
and Ranchi. National Highway routing still runs first.

Reviewed neutral handoffs are [Gujarat eNagar](https://enagar.gujarat.gov.in/enagar/login.jsp),
[Haryana Nagar Darshan](https://nagardarshan.ulbharyana.gov.in/Default/CitizenEntry),
[Jharkhand Municipal Grievance](https://municipalservices.jharkhand.gov.in/public/grievance_new/login),
[JK Samadhan](https://samadhan.jk.gov.in/).

These relevance envelopes and map markers are not municipal polygons and do not claim
the complete Census Urban Agglomeration. A stale state label, place text outside the
envelope, missing geocode, boundary-touching accuracy circle, pack failure, or ambiguous
match fails closed. Accepted routes remain neutral: the user must select and verify the
district, department, local body, issue category, and road owner in the external service.
No route proves ownership, guarantees category acceptance, or files automatically. A
separate optional catalog may add an unawarded notice candidate under the rules above.

## Delhi NCT boundary and complaint handoffs

Delhi-specific coverage is the full National Capital Territory, not the wider National
Capital Region. Noida, Gurugram, Ghaziabad, Faridabad, and other NCR cities outside Delhi
NCT cannot inherit the Delhi recipient. Noida and Ghaziabad can qualify separately through
the exact Uttar Pradesh route, while Faridabad retains its conservative top-50 neutral
route. The Delhi routing pack contains the polygon for
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

Every accepted Delhi road-damage point is offered the Government of NCT of Delhi's
[PWD Sewa complaint flow](https://www.pwddelhi.gov.in/sewa/complaint). PWD Sewa's current
dashboard records road and pothole grievances and forwarding to other Delhi agencies, so
it is used as a coordination handoff rather than labelled as the confirmed owner. The
official Android package is `com.sis.pwdsewaapp`; the app also offers the published
WhatsApp number **+91 81301 88222** and helpline **1908**. The cross-department
[Delhi PGMS](https://pgms.delhi.gov.in/) is the alternate. Opening any channel is not a
submission; the citizen must complete the external complaint and record its official
reference ID.

Garbage and manhole reports use GNCTD/NIC's
[CM JanSunwai](https://cmjansunwai.delhi.gov.in/) as a cross-department grievance
coordination handoff, with Delhi PGMS as the alternate and helpline **1902**. The official
page documents complaint filing, tracking, reminders, feedback, and the linked JanMitra
Android app; its [app privacy page](https://cmjansunwai.delhi.gov.in/PrivacyPolicyApp)
confirms the Government of NCT of Delhi service. This route does not identify a municipal
owner or guarantee a category outcome.

Delhi has no authoritative road-linked award, maintenance, and defect-liability feed for
all road agencies. Its public GePNIC pack can add a same-State open-notice candidate only.

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

The specific GCC route is GCC only, not the wider Chennai Metropolitan Area. Adjacent urban
bodies are excluded from the GCC route but can use the neutral statewide Tamil Nadu route
when their full GPS-accuracy circle is inside the state boundary. GCC containment does not
establish that GCC owns or maintains a road. The primary
handoff is [GCC Public Grievance](https://erp.chennaicorporation.gov.in/pgr/citizen/BeforeReg.do);
the published Android package is
[`com.ceedeev.grivenancev2`](https://play.google.com/store/apps/details?id=com.ceedeev.grivenancev2).
The app also offers GCC's published WhatsApp number **+91 94450 61913** and helpline
**1913**. Opening any channel is not submission. A same-State open-notice candidate is
not evidence that GCC owns or awarded work on the road.

## Hyderabad CURE official point query and shared complaint handoff

The exact Hyderabad routing pack contains query metadata but no CURE polygon; the separate
statewide Telangana pack contains the ODbL state polygon described above. In the native
Android app, an accepted GPS fix (at most 30 m reported accuracy) is converted to its complete
accuracy envelope. Before the statewide fallback, the app asks TGRAC's official
[`Proposed Core Urban Area -2053 Sq.Km` layer 22](https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer/22)
whether that entire envelope is within the Core Urban Region. The route is accepted only when
the service returns one containing feature. This covers the official 2,053 km² CURE service
scope without redistributing government geometry.

The same request checks TGRAC's exact official
[`Cantonment Boundary` layer 1](https://tgrac.telangana.gov.in/arcgis/rest/services/Hydra_Folder/Administrative_Layer/MapServer/1).
Any accuracy envelope intersecting Secunderabad Cantonment is refused for My Cure. If either
official query is unavailable, malformed, or inconclusive, the app does not claim CURE
containment or choose My Cure. Browser/PWA use likewise does not send a Hyderabad coordinate
to TGRAC. In each case, the independently verified local state polygon can still offer the
neutral Telangana Prajavani fallback; this does not identify a municipal or Cantonment road
owner.

[G.O.Ms.No.292 dated 24 December 2025](https://goir.telangana.gov.in/) reorganised the
expanded area into 12 zones and 60 circles. Telangana's
[G.O.Ms.No.55 dated 11 February 2026](https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/GO.Ms.No.55_11-02-2026.pdf)
then constituted Greater Hyderabad, Cyberabad, and Malkajgiri as three separate municipal
corporations. The CURE query does not establish which corporation or road maintainer owns a
point, so the app makes no such attribution. It offers the shared
[My Cure](https://play.google.com/store/apps/details?id=cgg.gov.ghmc) civic-grievance handoff,
whose current listing expressly includes pothole grievances, plus its
[web flow](https://igs.ghmc.gov.in/operator/send_otp_mobile). Opening either service is not
submission; the OTP-bound web flow is not a public complaint-write API. Telangana's
dedicated official tender feed is described in the statewide section above.

## Ahmedabad AMC boundary and complaint handoff

The Gujarat pack contains a dissolved union of the 48 Ahmedabad Municipal Corporation ward
polygons from [OpenCity / Oorvani Foundation via Bharatlas](https://bharatlas.com/view/wards_ahmedabad),
published under the ODbL 1.0. The source snapshot is dated 26 May 2026 and was retrieved on
23 August 2026; the downloaded GeoJSON SHA-256 was
`c5015c0cd147118e34ddf60fccce4f4c93d72118b21ae5d5dc36d1723c17043a`. The build validates
one copy of every ward code 1–48, corrects the source file's reviewed latitude/longitude
axis order, dissolves internal ward edges, rounds to seven decimal places, and pins the
resulting geometry digest
`48de18a521d2ece507ebda91976064353b477283a251bf45d271fdd0c7b82cb7`.

The bundled union covers about 439.397 km², with bounds 72.4472434–72.7036946 E and
22.9121407–23.1386475 N. AMC's official corporation page independently states that the
city has [seven zones and 48 wards](https://ahmedabadcity.gov.in/Home/AboutTheCorporation).
AMC materials also publish larger total areas after outer-city mergers, so this snapshot
cannot prove that every current expansion is included. The geometry is a reviewed routing
aid, not a legal survey or proof that AMC owns a particular road. The wider AUDA planning
area and neighbouring municipal bodies remain outside this route.

A contained point is offered AMC's official
[CCRS online complaint flow](https://www.amccrs.com/AMCPortal/View/ComplaintRegistration.aspx?m=Online).
The current citizen Android package is
[`com.amplvb.ccrs`](https://play.google.com/store/apps/details?id=com.amplvb.ccrs).
AMC's [channel instructions](https://www.amccrs.com/AMCPortal/View/ComplainRegistrationMobile.aspx)
also publish WhatsApp **+91 75678 55303**, helpline **155303**, and
`ccrs@ahmedabadcity.gov.in`. The user must finish the complaint in CCRS. The dedicated
Gujarat adapter reads nProcure's anonymous public tenders-in-progress table through its
normal browser flow and preserves official references, titles, organisations, deadlines,
values, and POST locators. It asserts no contractor, award, segment, warranty, or DLP.

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

The 26 August 2026 live audit read **98,499** official `AWARDED` works across 99 pages.
Strict carriageway scope retained **21,924** procurement records and rejected **76,575**;
zero rows were structurally invalid. The 47,306,026-byte audit output is deliberately not
committed or shipped. A small receipt preserves its SHA-256 and exact accounting. `AWARDED`
is only the portal's search status: it does not prove current execution, contractor, exact
segment, warranty, or DLP.

The current downloadable municipal pack remains the separately reviewed 21 August source:
**42,283** broad source rows, of which **13,577** are indexed to a supported
municipal body, only **5,351** pass that same classifier and enter the downloadable pack.
Drain, footpath, UGD/sewer, pipeline, lighting, building, bridge, culvert, and similar
roadside-only work is excluded when a road name merely describes its location. Mixed work
is retained only when work on the road surface is explicit.

**Verification done on 19 Aug 2026:** of the 1,000 rows on the portal's first page,
**341 appear in the full 42,283-row source snapshot, byte-identical on title, publication
date and location**. This check predates the supported-body and carriageway-scope
reductions and is not a claim that all 341 appear in the current 5,351-row downloadable
pack.

Fields taken: `tenderNumber`, `description` (the work title), `locationName`,
`publishedDate`. Nothing is edited beyond truncating long titles.

## Contractor names

**Source: the public-domain snapshot at
<https://bengaluru-road-contracts.pages.dev>, whose own source is KPPP.**

The portal's *search* results do not include the winning bidder; only the per-tender full
view does. Contractor names therefore come from that pre-existing Bengaluru snapshot.
The full 42,283-row source has a bidder recorded on **1,124** rows, covering **993**
distinct names; the filtered
downloadable pack has a bidder recorded on **526** rows, covering **492** distinct names.
Its other **4,825** rows have no bidder name. Outside Bengaluru
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

### Bengaluru general civic handoff

For garbage and manhole reports inside any of the five configured Bengaluru city
corporations, the app offers **Namma Bengaluru (Sahaaya 2.0)** instead of borrowing a
road-recipient email. The current [Greater Bengaluru Authority site](https://gba.karnataka.gov.in/)
lists all five corporations, Sahaaya 2.0 as a public-grievance service, Namma Bengaluru
under mobile apps, and helpline **1533**. BBMP's official
[mobile-app directory](https://site.bbmp.gov.in/departmentwebsites/BBMPIT/mobileapps.html)
says Sahaaya 2.0 lets citizens file complaints and check status, and its
[administrative review](https://bbmp.gov.in/ucc_file/KarnatakaAdministrativeReforms.pdf)
describes the unified platform, integrated agencies, and text/photo/video evidence. The
Android package is
[`com.nammabengaluruNew.org`](https://play.google.com/store/apps/details?id=com.nammabengaluruNew.org).
Public pages do not enumerate every garbage/manhole subcategory, so this is labelled a
general public-grievance handoff. Outside those verified Bengaluru routes, the exact
state boundary can offer neutral Janaspandana, with Janahitha as an urban alternate; it
does not assign a municipal recipient or guarantee category acceptance.

## Street address

**Source: OpenStreetMap, via Nominatim.** It turns coordinates into a street name and
pincode for the complaint. For all state/UT and exact polygon routes, address fields are
display-only routing clues; only a verified cached polygon selects statewide, Union
Territory, or local polygon coverage. Exact Hyderabad CURE
coverage instead requires the live official TGRAC response described above. Free-form
geocoder text is never used to expand any polygon route. The 8 additional top-50 routes
are the explicit exception: they require
exact configured `city` or `municipality` and state fields inside a conservative coordinate
envelope and never use free-form display text. The coordinates themselves
come from the phone's GPS and are printed in the complaint alongside a map link, so the
location can be checked independently.

## What is generated or inferred

The pothole verdict, its size and the one-line description are produced by an AI vision
model looking at the photograph, and the app says so. They are a judgement about a
photograph, not a record, which is why the photograph is always attached: the officer can
disagree by looking.

A candidate match is a judgment layered over a procurement/project record. An eligible
Karnataka route uses a deterministic local shortlist and an AI adjudication against the
same civic-body pool. National Highway, PMGSY, and official State/UT notice candidates use deterministic
same-State NH/NE or road/immediate-locality evidence. A PMGSY source-reported agreement is
not treated as a verified award. None of these transforms a title match into proof of
segment responsibility.

Warranty or DLP is never inferred from publication date. It remains unverified unless an
authoritative cited record explicitly supplies the relevant segment and period.

## Known limits

- All 28 states and 8 Union Territories have checksum-pinned ODbL boundary containment
  and a conservative official grievance handoff. The complete GPS-accuracy circle must
  stay inside one jurisdiction; border ambiguity fails closed. National Highway and exact
  city/authority routes retain precedence. Containment does not prove road ownership,
  identify the responsible local body, guarantee category acceptance, or submit anything.
- Maharashtra-wide evidence and official-handoff coverage uses a pinned ODbL state
  boundary. Outside exact MMR and PMC routes, Aaple Sarkar is neutral: it does not prove
  the responsible department, complaint category, road owner, or successful submission.
- MMR coverage targets the official notified extent, but its downloaded OpenStreetMap outer
  outline is approximate, differs from MMRDA's published area by about 1%, and does not
  subtract the Scheduled Areas excluded by the 2019 notification. It must not be used as
  a legal boundary. Eleven available civic polygons can select a body; eight bodies,
  rural points, and ambiguities fall back to Aaple Sarkar. A suggestion does not establish
  road ownership.
- PMC-specific routing uses a verified copy of PMC's official GIS boundary. PCMC remains
  in statewide Maharashtra coverage but is not labelled or routed as PMC. A later PMC
  boundary change requires a reviewed state-pack update.
- West Bengal-wide evidence and official-handoff coverage uses a pinned ODbL state
  boundary. Outside the exact KMC polygon, PGRS is neutral: it does not prove the
  responsible district, department, local body, complaint category, road owner, or
  successful submission.
- The KMC-specific route uses a verified, validity-repaired copy of the official West
  Bengal UDMA municipal feature. Howrah, Bidhannagar/Salt Lake, and New Town use the
  statewide fallback rather than KMC. A later KMC boundary change requires a reviewed
  state-pack update.
- Punjab-wide coverage uses a pinned ODbL state boundary and excludes Chandigarh Union
  Territory. Connect Punjab is a neutral handoff; it does not identify the responsible
  department, local body, road owner, category, or successful submission.
- Tamil Nadu-wide coverage uses a pinned ODbL state boundary and excludes Puducherry Union
  Territory, including Karaikal. Outside the exact GCC route, Mudhalvarin Mugavari is a
  neutral handoff. It does not identify
  the responsible department, local body, owner, category, contractor, or submission.
- Andhra Pradesh-wide coverage uses a pinned ODbL state boundary and explicitly excludes
  Yanam, Puducherry Union Territory. PGRS and the urban Puramithra alternate are neutral
  handoffs; neither identifies the department, local body, road owner, category, contractor,
  or submission.
- Telangana-wide coverage uses a pinned ODbL state boundary checked against TGRAC's
  official state layer. Prajavani is neutral, and Citizen Buddy is only an urban alternate
  outside Hyderabad; neither identifies a department, local body, road owner, category,
  contractor, or submission.
- Karnataka-wide coverage uses a pinned ODbL state boundary. Verified municipal and
  Bengaluru routes remain preferred; other contained points use neutral Janaspandana,
  with Janahitha offered only as an urban alternate. Neither service proves the responsible
  department, local body, owner, category, contractor, or submission.
- Kerala-wide coverage uses a pinned ODbL state boundary and excludes Mahe. The Kerala CMO
  portal is neutral, and K-SMART is a local-body alternate; neither identifies the owner,
  category, contractor, or submission.
- Uttar Pradesh-wide coverage uses a pinned ODbL state boundary and excludes Delhi NCT.
  Jansunwai–Samadhan is neutral; it does not identify the responsible department, local
  body, road owner, category, contractor, or submission.
- Chhattisgarh-wide coverage uses a pinned ODbL state boundary. The CM Helpline is neutral,
  and NIDAAN 1100 is only an urban civic alternate; neither identifies the responsible
  department, local body, road owner, category, contractor, or submission.
- Rajasthan-wide coverage uses a pinned ODbL state boundary. Rajasthan Sampark is neutral;
  it does not identify the responsible department, local body, road owner, category,
  contractor, or submission.
- Goa-wide coverage uses a pinned ODbL state boundary. CM Helpline Goa is neutral; it does
  not identify the responsible department, local body, road owner, category, contractor,
  or submission.
- Madhya Pradesh-wide coverage uses a pinned ODbL state boundary. CM Helpline is neutral;
  it does not identify the responsible district, department, local body, road owner,
  category, contractor, or submission.
- Bihar-wide coverage uses a pinned ODbL state boundary. Lok Shikayat is neutral and its
  statutory service scope must be verified; it does not identify a public authority,
  local body, road owner, category, contractor, or submission.
- Odisha-wide coverage uses a pinned ODbL state boundary. Jana Sunani is neutral; the user
  must choose and verify the district/block-or-ULB and recipient office. It does not prove
  an owner, category, contractor, or submission.
- The 8 additional top-50 routes use conservative Nominatim search envelopes plus exact
  structured city/municipality and state aliases. They are not municipal polygons or
  complete Census Urban Agglomeration boundaries; a missing, stale, ambiguous, or
  boundary-touching match fails closed.
- Delhi-specific coverage is the full NCT outline only. Other NCR points never inherit the
  Delhi recipient; Noida and Ghaziabad may independently use the Uttar Pradesh route, while
  Faridabad retains its top-50 neutral route. Every accepted Delhi point uses a cross-agency grievance handoff because the
  outline cannot establish whether PWD, MCD, NDMC, Cantonment, DDA, NHAI, or another
  agency maintains the road.
- The Chennai-specific route is GCC only. Its ODbL polygon was checked against, but is not
  copied from, GCC's official 2025 layer; later boundary changes require a reviewed pack
  update. Other confidently contained Tamil Nadu points use the statewide neutral route.
- Hyderabad's specific My Cure route uses the official 2,053 km² CURE point-query service
  on Android. The complete GPS-accuracy envelope must be within CURE and must not intersect
  the exact official Secunderabad Cantonment layer. The web, an unavailable service, or a
  Cantonment intersection cannot select My Cure, but a verified in-state point can use the
  neutral Prajavani fallback. Neither route assigns one of the three 2026 corporations.
- Ahmedabad coverage uses the pinned 439.397 km² ODbL union of 48 AMC wards. AMC materials
  publish larger post-merger areas, so complete current outer-expansion coverage is not
  proven. The wider AUDA area is excluded.
- There is no automatic filing, status sync, or cross-user report database for any route.
  Evidence remains local until the citizen deliberately opens an external handoff.
- No State/UT boundary establishes a contract, road owner, or maintaining agency. The
  nationwide candidate layers do not guarantee every tender or road. PMGSY rows verify
  only source-reported agreement fields; official procurement notices remain unawarded
  candidates; and Andhra Pradesh remains a documented portal-access gap. Exact segment,
  award, contractor, warranty, and DLP stay unknown
  without authoritative evidence.
- 137 of Karnataka's 319 local bodies have no address in the file because their district
  pages publish none. Those points cannot receive a specific municipal recipient, but can
  use neutral Janaspandana after exact state containment.
- The full 42,283-row source has 41,159 records without a winning bidder. The filtered
  5,351-row pack has 4,825 without one and 526 with one.
- Of the 18,972 DMA or legacy-BBMP source rows, 7,473 explicitly include carriageway work;
  5,351 are indexed to a supported body and enter the pack, while 2,122 are unresolved or
  belong to bodies without a published address. The 23,311 non-municipal source rows are
  not candidates for a municipal complaint.
- Scope classification is deliberately fail-closed and title-based. It may omit a genuine
  road contract whose title is vague, but a roadside-only contract cannot be rescued by an
  AI location match or by a road-looking tender-number category.
- Contracts are a snapshot. Re-run `tools/pull-kppp.py` to refresh.
- The public Nominatim endpoint is cached on an approximately 11 m grid and serialized
  below one request per second. National or municipal-scale deployment must switch the
  endpoint to a policy-compliant managed or self-hosted service. The 8 additional city
  routes depend on exact Nominatim structured fields. All state/UT and other downloaded
  polygon routing remains local and does not depend on a geocoder response. Native Android
  Hyderabad routing sends the GPS-accuracy envelope to TGRAC's official CURE and Cantonment
  query services.
