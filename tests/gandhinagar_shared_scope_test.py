"""A Karnataka-only resolver must not describe Gujarat as outside Indian coverage."""
from playwright.sync_api import sync_playwright
from central_stub_harness import Central, open_central
from state_pack_utils import read_pack, route_pattern

with sync_playwright() as p:
    browser, page, dialogs, errors = open_central(p, Central())
    try:
        _, raw = read_pack('in-gj-state-routing')
        page.route(route_pattern('in-gj-state-routing'), lambda route: route.fulfill(
            status=200, content_type='application/json', body=raw))
        result = page.evaluate('''async()=>{
          const P=StandaloneAPI.__pure;
          const route=await P.routeOfficer(null,23.181854,72.652801,12,null,null,
            'road_damage',{road_ownership:'outside_state'});
          const uncertain=await P.routeOfficer(null,23.181854,72.652801,500,null,null,
            'road_damage',{road_ownership:'outside_state'});
          const unknown=await P.routeOfficer(null,23.181854,72.652801,12,null,null,
            'road_damage',{road_ownership:'unknown'});
          const report={status:'unrouted',unrouted_reason:route.unrouted_reason,
            lat:23.181854,lng:72.652801};
          return {route,uncertain,unknown,title:unroutedTitle(report),
            chip:chip(report.status,report),retry:canRetryRouting(report),
            oldRetry:canRetryRouting({...report,unrouted_reason:'outside_area'})};
        }''')
        assert result['route']['unrouted_reason'] == 'regional_email_unavailable', result
        assert not result['route'].get('officer_email'), result
        assert result['uncertain']['unrouted_reason'] == 'location_uncertain', result
        assert result['unknown']['unrouted_reason'] == 'road_class_unknown', result
        assert 'Outside coverage' not in result['chip'], result
        assert result['retry'] and result['oldRetry'], result
        assert not errors, errors
        print('PASS Gandhinagar shared scope, email safety, GPS uncertainty, retry and UI')
    finally:
        browser.close()
