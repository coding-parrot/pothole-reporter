# -*- coding: utf-8 -*-
"""The home screen must lead with one-word, highlighted Drive action."""
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")

SCENARIO = r"""
() => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const word = (html) => {
    const node = document.createElement("div");
    node.innerHTML = html;
    return node.textContent.replace(/^[^\p{L}\p{N}]+/u, "").trim();
  };

  const buttons = [...document.querySelectorAll("#home .home-actions > button")];
  eq("order: Drive is the first home action",
     buttons.map((button) => button.id), ["driveBtn", "captureBtn", "dashBtn"]);
  ok("hierarchy: Drive alone uses the primary style",
     buttons[0].classList.contains("primary")
       && !buttons[1].classList.contains("primary")
       && !buttons[2].classList.contains("primary"),
     buttons.map((button) => button.className));
  ok("hierarchy: Drive renders in the orange highlight",
     getComputedStyle(buttons[0]).backgroundColor === "rgb(255, 122, 26)",
     getComputedStyle(buttons[0]).backgroundColor);
  ok("layout: Drive is visually above Photo and the map",
     buttons[0].getBoundingClientRect().top < buttons[1].getBoundingClientRect().top
       && buttons[1].getBoundingClientRect().top < buttons[2].getBoundingClientRect().top,
     buttons.map((button) => button.getBoundingClientRect().top));
  // The map is a different kind of action from reporting one, so it is set apart rather
  // than stacked flush against Photo.
  ok("layout: the map button is separated from Photo",
     buttons[2].getBoundingClientRect().top
       - buttons[1].getBoundingClientRect().bottom >= 8,
     buttons[2].getBoundingClientRect().top - buttons[1].getBoundingClientRect().bottom);

  // The map button says what it opens. The other two stay single words; this one is a
  // name for a place in the app, so it is allowed the two words that name it.
  const expected = {
    en: ["Drive", "Photo", "Pothole map"],
    kn: ["ಡ್ರೈವ್", "ಫೋಟೋ", "ಗುಂಡಿ ನಕ್ಷೆ"],
    mr: ["ड्राइव्ह", "फोटो", "खड्डे नकाशा"],
    bn: ["ড্রাইভ", "ছবি", "গর্তের মানচিত্র"],
  };
  for (const [language, labels] of Object.entries(expected)) {
    const actual = [I18N[language].drive_btn, I18N[language].report_btn,
                    I18N[language].dash_btn].map(word);
    eq(`copy: ${language} home actions are the approved words`, actual, labels);
    ok(`copy: ${language} the two capture actions are single words`,
       actual.slice(0, 2).every((label) => !/\s/u.test(label)), actual.slice(0, 2));
    ok(`copy: ${language} the map label stays short`,
       actual[2].split(/\s+/u).length <= 2, actual[2]);
  }

  eq("render: English home actions match their localized values",
     buttons.map((button) => button.textContent.replace(/^[^\p{L}\p{N}]+/u, "").trim()),
     expected.en);

  // Android asks for 48 dp touch targets; the gear once measured 48x46.
  const gear = document.getElementById("gearBtn").getBoundingClientRect();
  ok("touch: the Settings gear is at least 48x48",
     gear.width >= 48 && gear.height >= 48, [gear.width, gear.height]);

  // An active drive is announced in the tester's language, not a fixed English string.
  setDriveActiveButton(true);
  eq("a11y: the active Drive button is labelled from I18N",
     buttons[0].getAttribute("aria-label"), I18N[LANG].drive_active_label);
  setDriveActiveButton(false);
  eq("a11y: the idle Drive button is labelled by its visible text",
     buttons[0].getAttribute("aria-label"), null);
  return checks;
}
"""

# What the phone paints before 1 MB of script has run: the markup itself. It must say
# what the English table says, and a translated install must not flash it at all.
MARKUP = r"""
async () => {
  const raw = await (await fetch(location.href, {cache: "no-store"})).text();
  const doc = new DOMParser().parseFromString(raw, "text/html");
  const text = (html) => {
    const node = document.createElement("div");
    node.innerHTML = html;
    return node.textContent.trim();
  };
  return {
    markup: ["driveBtn", "captureBtn", "dashBtn", "subTitle"]
      .map((id) => doc.getElementById(id).textContent.trim()),
    english: [I18N.en.drive_btn, I18N.en.report_btn, I18N.en.dash_btn, I18N.en.sub].map(text),
    pendingInMarkup: doc.documentElement.classList.contains("i18n-pending")
      || doc.getElementById("home").classList.contains("i18n-pending"),
    pendingLive: !!document.querySelector(".i18n-pending"),
  };
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('openai_key', 'test-key-never-sent');"
            "localStorage.setItem('app_lang', 'en');"
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof I18N !== 'undefined' && document.getElementById('driveBtn')",
            timeout=30000,
        )
        results = page.evaluate(SCENARIO)
        markup = page.evaluate(MARKUP)
        results.append(["markup: first-paint home copy equals I18N.en",
                        markup["markup"] == markup["english"], markup["markup"], markup["english"]])
        results.append(["markup: home is held until applyLang runs",
                        markup["pendingInMarkup"] and not markup["pendingLive"],
                        markup, "held in markup, released live"])
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"HOME ACTIONS TEST PASS ({len(results)} checks)")


main()
