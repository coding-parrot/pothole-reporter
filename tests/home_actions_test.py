# -*- coding: utf-8 -*-
"""The home screen must lead with one-word, highlighted Drive action."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

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

  const buttons = [...document.querySelectorAll("#home > button")];
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
  ok("layout: Drive is visually above Photo and Contribution",
     buttons[0].getBoundingClientRect().top < buttons[1].getBoundingClientRect().top
       && buttons[1].getBoundingClientRect().top < buttons[2].getBoundingClientRect().top,
     buttons.map((button) => button.getBoundingClientRect().top));

  const expected = {
    en: ["Drive", "Photo", "Contribution"],
    kn: ["ಡ್ರೈವ್", "ಫೋಟೋ", "ಕೊಡುಗೆ"],
    mr: ["ड्राइव्ह", "फोटो", "योगदान"],
    bn: ["ড্রাইভ", "ছবি", "অবদান"],
  };
  for (const [language, labels] of Object.entries(expected)) {
    const actual = [I18N[language].drive_btn, I18N[language].report_btn,
                    I18N[language].dash_btn].map(word);
    eq(`copy: ${language} home actions are the approved words`, actual, labels);
    ok(`copy: ${language} actions are each one word`,
       actual.every((label) => !/\s/u.test(label)), actual);
  }

  eq("render: English home actions match their localized values",
     buttons.map((button) => button.textContent.replace(/^[^\p{L}\p{N}]+/u, "").trim()),
     expected.en);
  return checks;
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
