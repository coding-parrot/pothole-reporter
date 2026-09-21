# -*- coding: utf-8 -*-
"""Text a tester can read, targets a thumb can hit, and actions they can reach.

Each case was measured on a phone-sized viewport:
  - the viewer's pinch hint was 12 px grey straight on the photo, about 1.3:1 on road
    pixels in landscape;
  - Home's three actions sat 14 px then 12 px apart at three heights, 32 px above
    "Your reports";
  - body text on Home, detail, Drive, viewer and Feedback was 11 to 13 px;
  - the nudge buttons, map Refresh, the Feedback stars and fields were under 44 px;
  - no color-scheme, so form controls drew light on the dark cards and the Feedback
    placeholder, its only prompt, was 4.0:1;
  - in landscape, detail and Feedback put Back, Delete and Send below the fold.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow, report_form_script


failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


CONTRAST = """
  const rgb = (value) => (value.match(/[\\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= .03928 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4); };
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(rgb(a)), lum(rgb(b))].sort((p, q) => q - p);
    return (x + .05) / (y + .05);
  };
"""

PIXEL_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="


def rect(page, selector):
    return page.evaluate(f"""() => {{
      const r = document.querySelector({selector!r}).getBoundingClientRect();
      return {{ top: r.top, bottom: r.bottom, height: r.height, width: r.width }};
    }}""")


with sync_playwright() as p:
    browser, page, errors = open_flow(p, storage={"feedback_nudged": "0"})
    try:
        page.set_viewport_size({"width": 412, "height": 915})
        page.wait_for_function("() => !document.getElementById('home').classList.contains('i18n-pending')")

        # pixels-a11y-9: equal gaps and matching heights for the Home actions.
        drive, photo, dash = (rect(page, s) for s in ("#driveBtn", "#captureBtn", "#dashBtn"))
        title = rect(page, "#reportsTitle")
        gaps = [round(photo["top"] - drive["bottom"]), round(dash["top"] - photo["bottom"])]
        check(abs(gaps[0] - gaps[1]) <= 1, f"pixels-a11y-9: Home action gaps differ: {gaps}")
        check(abs(photo["height"] - dash["height"]) <= 1,
              f"pixels-a11y-9: Photo is {photo['height']} px, Pothole map {dash['height']} px")
        check(title["top"] - dash["bottom"] <= 20,
              f"pixels-a11y-9: {title['top'] - dash['bottom']:.0f} px from the map button to the list")

        # pixels-a11y-12: a dark page says so, and the placeholder reads.
        scheme = page.evaluate(CONTRAST + """
          const text = document.getElementById('feedbackText');
          return {
            root: getComputedStyle(document.documentElement).colorScheme,
            meta: (document.querySelector('meta[name=color-scheme]') || {}).content || null,
            placeholder: ratio(getComputedStyle(text, '::placeholder').color,
                               getComputedStyle(text).backgroundColor),
          };""".join(["() => {", "}"]))
        check(scheme["root"] == "dark" and scheme["meta"] == "dark",
              f"pixels-a11y-12: color-scheme is not dark: {scheme}")
        check(scheme["placeholder"] >= 4.5,
              f"pixels-a11y-12: placeholder contrast {scheme['placeholder']:.2f}:1")

        # pixels-a11y-11: touch targets on Home with the nudge, the map and Feedback.
        page.evaluate("document.getElementById('feedbackNudge').classList.remove('hidden')")
        small = page.evaluate("""() => ['feedbackNudgeDismiss', 'feedbackNudgeOpen']
          .map((id) => [id, document.getElementById(id).getBoundingClientRect().height])
          .filter(([, h]) => h < 44)""")
        check(not small, f"pixels-a11y-11: nudge buttons under 44 px: {small}")
        refresh = page.evaluate("""() => {
          const b = document.getElementById('dashRefresh');
          const s = getComputedStyle(b);
          return parseFloat(s.minHeight) || 0; }""")
        check(refresh >= 44, f"pixels-a11y-11: map Refresh min-height is {refresh} px")
        page.evaluate("show('feedback')")
        small = page.evaluate("""() => [...document.querySelectorAll(
            '#feedbackStars button, #feedbackMode, #feedbackEmail, #feedbackBack, #feedbackSend')]
          .map((el) => [el.id || el.dataset.rating, Math.round(el.getBoundingClientRect().height)])
          .filter(([, h]) => h < 44)""")
        check(not small, f"pixels-a11y-11: Feedback targets under 44 px: {small}")

        # pixels-a11y-10: body text is at least 14 px, chips at least 12 px.
        sizes = page.evaluate("""() => {
          const px = (el) => parseFloat(getComputedStyle(el).fontSize);
          const chip = document.createElement('span');
          chip.className = 'chip draft'; chip.textContent = 'Draft';
          document.body.appendChild(chip);
          const out = {
            meta: px(document.getElementById('feedbackIntro')),
            label: px(document.getElementById('feedbackModeLabel')),
            sub: px(document.getElementById('subTitle')),
            viewerMeta: px(document.getElementById('viewerMeta')),
            viewerHint: px(document.getElementById('viewerHint')),
            driveStatus: px(document.getElementById('driveStatus')),
            chip: px(chip),
          };
          chip.remove();
          return out;
        }""")
        low = {k: v for k, v in sizes.items() if v < (12 if k == "chip" else 14)}
        check(not low, f"pixels-a11y-10: text below its floor: {low}")
        page.evaluate("show('home')")

        # pixels-a11y-8: the landscape viewer hint sits on its own dark pill.
        page.set_viewport_size({"width": 915, "height": 412})
        page.evaluate(f"""() => openViewer({{id: 7, status: 'draft', assessment: 'damaged',
          damage_type: 'surface_breakup', size: 'medium', description: 'Broken road.',
          photo_url: {PIXEL_GIF!r}}})""")
        hint = page.evaluate(CONTRAST + """
          const h = document.getElementById('viewerHint');
          const s = getComputedStyle(h);
          const bg = s.backgroundColor;
          const alpha = (bg.match(/[\\d.]+/g) || []).map(Number)[3];
          return { bg, alpha: alpha === undefined ? 1 : alpha,
                   contrast: ratio(s.color, 'rgb(0,0,0)'),
                   width: h.getBoundingClientRect().width, size: parseFloat(s.fontSize) };""".join(
            ["() => {", "}"]))
        check(hint["alpha"] >= .7 and hint["width"] < 915 * .8,
              f"pixels-a11y-8: viewer hint has no pill behind it: {hint}")
        check(hint["contrast"] >= 4.5 and hint["size"] >= 14,
              f"pixels-a11y-8: viewer hint is hard to read: {hint}")
        page.evaluate("closeViewer()")

        # pixels-a11y-13: landscape detail and Feedback keep their actions in view.
        page.evaluate(report_form_script())
        page.evaluate("() => openDetail(window.__flowReport)")
        page.wait_for_selector("#backBtn")
        back = rect(page, "#backBtn")
        check(back["bottom"] <= 412, f"pixels-a11y-13: detail Back ends at {back['bottom']:.0f} px of 412")
        page.evaluate("show('feedback')")
        send = rect(page, "#feedbackSend")
        check(send["bottom"] <= 412, f"pixels-a11y-13: Feedback Send ends at {send['bottom']:.0f} px of 412")
        failures.extend(error_failures(errors, "screen legibility"))
    finally:
        browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS: readable text, 44 px targets, and reachable actions on a phone")
