"""National highways must refuse; city roads must still route."""
import os
from dotenv import load_dotenv
load_dotenv("/Users/gauravsen/Downloads/pothole-reporter/.env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app
KEY=os.environ["OPENAI_API_KEY"]
CASES=[  # name, lat, lng, should_route
 ("NH69 Chikkaballapur",13.4355,77.7315,False),
 ("NH48 Nelamangala",13.094709,77.389412,False),
 ("Bengaluru HSR",12.9115,77.6427,True),
 ("Bengaluru MG Road",12.9752,77.6068,True),
 ("Mysuru city",12.2958,76.6394,True),
 ("Hubballi",15.3647,75.1240,True),
]
JS="""async ([lat,lng]) => {
  const w = await StandaloneAPI.__probe(lat,lng);
  return w;
}"""
with sync_playwright() as p:
    b=p.chromium.launch(args=["--disable-web-security","--allow-running-insecure-content"])
    pg=b.new_context(viewport={"width":390,"height":844}).new_page()
    open_app(pg, KEY)
    fails=[]
    for name,lat,lng,should in CASES:
        r=pg.evaluate("""async ([lat,lng]) => {
            const url = (base,f) => base + '?geometry=' + encodeURIComponent(JSON.stringify({x:lng,y:lat,spatialReference:{wkid:4326}}))
              + '&geometryType=esriGeometryPoint&spatialRel=esriSpatialRelIntersects&outFields='+f+'&returnGeometry=false&f=json';
            const NH='https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/289/query';
            const TOWN='https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query';
            const [nh,tw]=await Promise.all([fetch(url(NH,'Name')).then(r=>r.json()),fetch(url(TOWN,'KGISTownName')).then(r=>r.json())]);
            return {nh:(nh.features||[]).length>0, town:((tw.features||[])[0]||{}).attributes};
        }""",[lat,lng])
        routes = (not r["nh"]) and bool(r["town"])
        tn = (r["town"] or {}).get("KGISTownName")
        status = "REFUSE (national highway)" if r["nh"] else (f"route -> {tn}" if r["town"] else "refuse (rural/outside)")
        ok = routes==should
        print(f"  {name:24} {status:34} {'ok' if ok else 'FAIL'}")
        if not ok: fails.append(name)
    b.close()
print()
if fails: print("FAIL:", fails); raise SystemExit(1)
print("NH GATE TEST PASS")
