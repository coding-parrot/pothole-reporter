"""Manual capture must use fused GPS, await slow fixes, and reject stale fixes."""
from playwright.sync_api import sync_playwright
from web_drive_harness import open_web_drive

with sync_playwright() as p:
    browser, page, dialogs, errors = open_web_drive(p)
    try:
        result = page.evaluate('''async () => {
          let browserCalls = 0, cleared = null, callback, release;
          const fix = () => ({coords:{latitude:12.97,longitude:77.59,accuracy:12},timestamp:Date.now()});
          navigator.geolocation.getCurrentPosition = () => { browserCalls++; };
          const geo = {
            getCurrentPosition: async () => { await new Promise(r => setTimeout(r, 2200)); return fix(); },
            checkPermissions: async () => ({location:'granted'}),
            watchPosition: async (options, cb) => {callback=cb;return 'photo-watch';},
            clearWatch: async ({id}) => {cleared=id;}
          };
          window.Capacitor = {isNativePlatform:()=>true,Plugins:{Geolocation:geo}};
          const slow = await getPosition(4000);
          geo.getCurrentPosition = async () => ({...fix(),timestamp:Date.now()-60000});
          const stale = await getPosition(100);
          geo.getCurrentPosition = () => new Promise(()=>{});
          const start=Date.now(); const hung=await getPosition(50);
          const elapsed=Date.now()-start;
          const sampler = startCapturePositionSampler();
          await new Promise(r=>setTimeout(r,0));
          callback(fix());
          const sampled = await sampler.positionAt(Date.now());
          const sampleCleared = cleared;
          geo.watchPosition = () => new Promise(r=>{release=r;});
          const late = startCapturePositionSampler();
          await new Promise(r=>setTimeout(r,0));
          late.stop(); release('late-photo-watch');
          await new Promise(r=>setTimeout(r,0));
          const lateCleared=cleared;
          let asked=0;
          geo.checkPermissions=async()=>({location:'prompt'});
          geo.watchPosition=()=>{asked++;};
          const noPermission=startCapturePositionSampler();
          await new Promise(r=>setTimeout(r,0)); noPermission.stop();
          return {slow,stale,hung,elapsed,browserCalls,sampled,sampleCleared,lateCleared,asked,
            unknownAccuracy:positionFix({coords:{latitude:12,longitude:77,accuracy:null}}).accuracy};
        }''')
        assert result['slow']['lat'] == 12.97, result
        assert result['stale'] is None and result['hung'] is None, result
        assert result['elapsed'] < 1000 and result['browserCalls'] == 0, result
        assert result['sampled']['lng'] == 77.59, result
        assert result['sampleCleared'] == 'photo-watch', result
        assert result['lateCleared'] == 'late-photo-watch', result
        assert result['asked'] == 0 and result['unknownAccuracy'] is None, result
        assert not errors, errors
        print('PASS native manual GPS, slow/stale/hung providers, sampler and permission/Stop races')
    finally:
        browser.close()
