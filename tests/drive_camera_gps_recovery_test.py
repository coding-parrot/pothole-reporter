"""Exercise live camera loss, GPS silence, zero-speed fixes and Stop races."""
from playwright.sync_api import sync_playwright
from web_drive_harness import open_web_drive

with sync_playwright() as p:
    browser, page, dialogs, errors = open_web_drive(p)
    try:
        page.locator('#driveBtn').click()
        page.wait_for_function('drive && drive.tally.checked > 0')
        # GPS can say stationary even as the driver turns the phone toward a pothole.
        page.evaluate('''() => {
          drive.stopLocation();
          drive.posSpeed = 0; drive.posAt = Date.now();
          window.before = drive.tally.captured;
        }''')
        page.wait_for_timeout(2400)
        assert page.evaluate('drive.tally.captured - before >= 2'), 'zero speed suppressed live checks'
        # Lose both sensors. Camera recovery must not sit behind the GPS gate.
        page.evaluate('''() => {
          drive.pos = null;
          window.oldStream = drive.stream;
          drive.stream.getTracks().forEach(t => t.stop());
        }''')
        page.wait_for_function('drive.stream !== oldStream && !drive.cameraRecovering', timeout=15000)
        assert page.evaluate('drive.stream.getVideoTracks()[0].readyState === "live"')
        # A watch that never calls back must recover through the independent probe.
        page.evaluate('''() => {
          const proto = Object.getPrototypeOf(navigator.geolocation);
          proto.watchPosition = () => 123;
          startDriveLocation(drive, p => {
            drive.pos = {lat:p.coords.latitude,lng:p.coords.longitude};
            drive.posAt = p.timestamp;
          }, () => {});
        }''')
        page.wait_for_function('drive.pos !== null', timeout=12000)
        # Stop while permission/acquisition is pending: a late stream must be closed.
        page.evaluate('''() => {
          const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia = async opts => {
            await new Promise(r => setTimeout(r, 700));
            window.lateStream = await real(opts); return lateStream;
          };
          drive.cameraRetryAt = 0;
          drive.stream.getTracks().forEach(t => t.stop());
          recoverDriveCamera(drive);
          stopDrive();
        }''')
        page.wait_for_function('window.lateStream && lateStream.getTracks().every(t => t.readyState === "ended")')
        page.wait_for_function('!driveFinalizing')
        native_watch = page.evaluate('''async () => {
          let release, cleared = null;
          window.Capacitor = {isNativePlatform: () => true, Plugins: {Geolocation: {
            watchPosition: () => new Promise(r => {release = r;}),
            clearWatch: async ({id}) => {cleared = id;},
            getCurrentPosition: async () => ({coords:{latitude:12,longitude:77},timestamp:Date.now()})
          }}};
          const ctx = {}; let delivered = 0;
          startDriveLocation(ctx, () => {delivered++;}, () => {});
          ctx.stopLocation(); release('late-native-watch');
          await new Promise(r => setTimeout(r, 30));
          return {cleared, delivered};
        }''')
        assert native_watch == {'cleared':'late-native-watch', 'delivered':0}, native_watch
        assert not errors, errors
        print('PASS zero-speed capture, camera recovery without GPS, silent GPS fallback, Stop recovery race')
    finally:
        browser.close()

    browser, page, dialogs, errors = open_web_drive(p, storage={'record_video': '1'})
    try:
        page.evaluate("localStorage.setItem('record_video', '1')")
        page.locator('#driveBtn').click()
        page.wait_for_function('drive && recCtx && recCtx.recorder && drive.tally.checked > 0')
        page.evaluate('''() => {
          window.session = drive; window.recording = recCtx;
          drive.stream.getTracks().forEach(t => t.stop());
        }''')
        page.wait_for_function('drive && !drive.cameraRecovering && recCtx === recording && recCtx.active && recCtx.seq >= 1', timeout=20000)
        page.wait_for_timeout(1200)
        page.locator('#driveStop').click()
        page.wait_for_function('!driveFinalizing', timeout=30000)
        assert page.evaluate('recording.seq >= 2 && recording.bytes > 0'), 'recovery lost recording segments'
        assert not errors, errors
        print('PASS recording survives camera recovery with continuous segment numbering')
    finally:
        browser.close()
