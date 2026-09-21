# -*- coding: utf-8 -*-
"""Settings must save what the tester chose, show the truth, and speak their language.

Each case below was a way the screen lied or lost work:
  - Save wrote record_video=0 over a recording turned on from the Drive HUD, and during
    a native drive it stopped that recording when only the name had changed;
  - gpt-5-mini with Original detail could be picked and was quietly saved as High;
  - any non-blank text was accepted as a personal OpenAI key;
  - Marathi and Bengali said Drive ignores the model choice, which it does not;
  - model and detail options and the gear's accessible name stayed English;
  - picking a language changed nothing until Save plus a reload;
  - Back, hardware Back and the Feedback round trip threw edits away without asking;
  - the key field invited autofill and could not be checked by eye;
  - the controls had no accessible names, the checkbox was squashed, the links tiny;
  - Save sat far below the fold on a small phone.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


def home_visible(page):
    return page.evaluate("() => !document.getElementById('home').classList.contains('hidden')")


def save(page):
    # A Save still settling keeps the button disabled; wait it out, then press it.
    page.wait_for_function("() => !document.getElementById('setSave').disabled")
    page.evaluate("() => document.getElementById('setSave').click()")
    page.wait_for_timeout(100)
    page.wait_for_function("() => !document.getElementById('setSave').disabled")


def settings_visible(page):
    return page.evaluate("() => !document.getElementById('settings').classList.contains('hidden')")


with sync_playwright() as playwright:
    # settings-5: a recording turned on from the Drive HUD survives an unrelated Save.
    browser, page, errors = open_flow(playwright, storage={"debug_mode": "0"})
    try:
        page.wait_for_function("() => typeof openSettings === 'function'")
        # What the HUD's Video button leaves behind in a running drive.
        page.evaluate("""async () => {
          window.__videoCalls = [];
          const plugin = Capacitor.Plugins.DriveMode;
          await plugin.setVideoRecording({ enabled: true });
          localStorage.setItem('record_video', '1');
          const original = plugin.setVideoRecording;
          plugin.setVideoRecording = (options) => { window.__videoCalls.push(options); return original(options); };
          drive = { native: true, stopping: false };
          openSettings();
        }""")
        page.fill("#setName", "Renamed Mid Drive")
        page.evaluate("() => document.getElementById('setSave').click()")
        page.wait_for_function("() => localStorage.getItem('sender_name') === 'Renamed Mid Drive'"
                               " && !document.getElementById('setSave').disabled")
        seen = page.evaluate("""() => {
          const out = { record: localStorage.getItem('record_video'), calls: window.__videoCalls };
          drive = null;
          return out;
        }""")
        check(seen["record"] == "1",
              f"settings-5: Save turned off a HUD recording, record_video={seen['record']!r}")
        check(not seen["calls"],
              f"settings-5: Save called setVideoRecording mid drive: {seen['calls']!r}")
        failures += error_failures(errors, "settings-5")
    finally:
        browser.close()

    browser, page, errors = open_flow(playwright)
    dialogs = []
    answer = {"confirm": False}

    def on_dialog(dialog):
        dialogs.append((dialog.type, dialog.message))
        if dialog.type == "confirm" and not answer["confirm"]:
            dialog.dismiss()
        else:
            dialog.accept()

    page.on("dialog", on_dialog)
    try:
        page.wait_for_function("() => typeof openSettings === 'function'")

        # settings-6: Original is only offered to the models that support it.
        config = page.evaluate("() => ({ models: LLM_UI_CONFIG.allowedModels,"
                               " originals: LLM_UI_CONFIG.originalDetailModels,"
                               " original: LLM_UI_CONFIG.originalImageDetail,"
                               " fallback: LLM_UI_CONFIG.defaultImageDetail })")
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "personal")
        capable = next(m for m in config["models"] if m in config["originals"])
        plain = next(m for m in config["models"] if m not in config["originals"])
        page.select_option("#setModel", capable)
        page.select_option("#setDetail", config["original"])
        page.select_option("#setModel", plain)
        state = page.evaluate(f"""() => ({{
          disabled: document.querySelector('#setDetail option[value="{config['original']}"]').disabled,
          value: document.getElementById('setDetail').value }})""")
        check(state["disabled"], f"settings-6: Original stays selectable with {plain}")
        check(state["value"] == config["fallback"],
              f"settings-6: {plain} left the detail on {state['value']!r}")
        page.select_option("#setModel", capable)
        enabled = page.evaluate(f"""() => !document.querySelector(
          '#setDetail option[value="{config['original']}"]').disabled""")
        check(enabled, f"settings-6: Original is not offered back for {capable}")

        # settings-7: a personal key must look like an OpenAI key before it is stored.
        answer["confirm"] = True
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "personal")
        page.fill("#setKey", "hello")
        before = len(dialogs)
        save(page)
        page.wait_for_timeout(300)
        check(settings_visible(page) and len(dialogs) == before + 1,
              f"settings-7: key 'hello' was accepted ({len(dialogs) - before} alert(s))")
        check(page.evaluate("() => localStorage.getItem('openai_key')") is None,
              "settings-7: key 'hello' was stored")
        good = "sk-" + "a1B2_c3-D4" * 4
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "personal")
        page.fill("#setKey", good)
        save(page)
        page.wait_for_function("() => localStorage.getItem('vision_provider') === 'personal'", timeout=10_000)
        check(page.evaluate("() => localStorage.getItem('openai_key')") == good,
              "settings-7: a well-formed key was not saved")
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "shared")
        save(page)
        page.wait_for_function("() => localStorage.getItem('vision_provider') === 'shared'")

        # settings-12: the key field neither autofills nor hides a paste from its owner.
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "personal")
        attrs = page.evaluate("""() => { const k = document.getElementById('setKey');
          return [k.getAttribute('autocomplete'), k.getAttribute('autocapitalize'),
                  k.getAttribute('spellcheck')]; }""")
        check(attrs == ["off", "off", "false"], f"settings-12: key field autofill guards {attrs!r}")
        reveal = page.locator("#keyReveal")
        check(reveal.count() == 1 and reveal.is_visible(), "settings-12: no Show control for the key")
        if reveal.count():
            reveal.click()
            check(page.evaluate("() => document.getElementById('setKey').type") == "text",
                  "settings-12: Show did not reveal the key")
            reveal.click()
            check(page.evaluate("() => document.getElementById('setKey').type") == "password",
                  "settings-12: Hide did not mask the key again")
        page.select_option("#setProvider", "shared")
        check(not reveal.is_visible(), "settings-12: the Show control stays on the shared path")

        # settings-11: leaving with edits asks first; the Feedback round trip keeps them.
        answer["confirm"] = False
        page.evaluate("() => openSettings()")
        page.fill("#setName", "Edited Not Saved")
        page.click("#feedbackBtn")
        page.click("#feedbackBack")
        check(page.input_value("#setName") == "Edited Not Saved",
              f"settings-11: Feedback round trip lost the edit ({page.input_value('#setName')!r})")
        before = len(dialogs)
        page.evaluate("() => document.getElementById('setBack').click()")
        check(len(dialogs) == before + 1 and dialogs[-1][0] == "confirm",
              "settings-11: Back discarded edits without asking")
        check(settings_visible(page), "settings-11: Back left Settings after the tester said stay")
        before = len(dialogs)
        page.evaluate("() => handleAppBack()")
        check(len(dialogs) == before + 1 and settings_visible(page),
              "settings-11: hardware Back discarded edits without asking")
        answer["confirm"] = True
        page.evaluate("() => document.getElementById('setBack').click()")
        check(home_visible(page), "settings-11: confirming the discard did not leave Settings")
        check(page.evaluate("() => localStorage.getItem('sender_name')") == "Test Citizen",
              "settings-11: a discarded edit was stored")
        before = len(dialogs)
        page.evaluate("() => openSettings()")
        page.evaluate("() => document.getElementById('setBack').click()")
        check(len(dialogs) == before and home_visible(page),
              "settings-11: Back without edits asked anyway")

        # settings-10: picking a language re-renders Settings at once.
        answer["confirm"] = True
        page.evaluate("() => openSettings()")
        page.select_option("#setLang", "kn")
        lang = page.evaluate("""() => ({ title: document.getElementById('settingsTitle').textContent,
          want: I18N.kn.settings_title, html: document.documentElement.lang })""")
        check(lang["title"] == lang["want"] and lang["html"] == "kn",
              f"settings-10: picking Kannada changed nothing before Save: {lang!r}")
        page.evaluate("() => document.getElementById('setBack').click()")
        reverted = page.evaluate("""() => ({ title: document.getElementById('settingsTitle').textContent,
          html: document.documentElement.lang, stored: localStorage.getItem('app_lang') })""")
        check(reverted["html"] == "en" and reverted["title"] == "Settings" and reverted["stored"] in (None, "en"),
              f"settings-10: Back kept the unsaved language: {reverted!r}")
        failures += error_failures(errors, "settings screen")
    finally:
        browser.close()

    # settings-8, settings-9, settings-13, settings-14: Kannada on a 360 px phone.
    browser, page, errors = open_flow(playwright, storage={"app_lang": "kn"})
    try:
        page.set_viewport_size({"width": 360, "height": 780})
        page.wait_for_function("() => typeof openSettings === 'function'")
        notes = page.evaluate("() => Object.fromEntries(Object.keys(I18N).map((l) =>"
                              " [l, [I18N[l].accuracy_note, I18N[l].key_label, I18N[l].model_label]]))")
        # The older build pinned Drive to gpt-5.6, applied the model to photos only and
        # said the key was sent from here to OpenAI; none of that is true any more.
        stale = ("gpt-5.6/high", "फोटो मॉडेल", "ছবির মডেল", "येथे जतन होते", "এখানে সংরক্ষিত")
        for language, texts in notes.items():
            for text in texts:
                check("gpt-5-mini" in texts[0] and not any(s in text for s in stale),
                      f"settings-8: {language} settings copy describes another build: {text!r}")
        page.evaluate("() => openSettings()")
        english = page.evaluate("""() => {
          const texts = [...document.querySelectorAll('#setModel option, #setDetail option')]
            .map((o) => o.textContent);
          return { leaked: texts.filter((s) => /baseline|experiment|High|Original|only/.test(s)),
                   gear: document.getElementById('gearBtn').getAttribute('aria-label'),
                   want: I18N.kn.settings_title };
        }""")
        check(not english["leaked"], f"settings-9: English options in Kannada: {english['leaked']!r}")
        check(english["gear"] == english["want"],
              f"settings-9: gear aria-label is {english['gear']!r} in Kannada")

        named = page.evaluate("""() => ['setLang', 'setProvider', 'setKey', 'setName', 'setModel',
          'setDetail', 'setDebug'].filter((id) => !document.getElementById(id).labels.length)""")
        check(not named, f"settings-13: controls with no label: {named!r}")
        box = page.evaluate("() => { const r = document.getElementById('setDebug')"
                            ".getBoundingClientRect(); return [r.width, r.height]; }")
        check(abs(box[0] - box[1]) < 0.5, f"settings-13: debug checkbox is {box[0]}x{box[1]}")
        for link in ("privacyLink", "sourcesLink"):
            height = page.evaluate(f"() => document.getElementById('{link}').getBoundingClientRect().height")
            check(height >= 44, f"settings-13: {link} is {height} px tall")

        page.evaluate("() => window.scrollTo(0, 0)")
        rect = page.evaluate("() => { const r = document.getElementById('setSave')"
                             ".getBoundingClientRect(); return [r.top, r.bottom]; }")
        check(rect[1] <= 780, f"settings-14: Save sits at {rect[0]:.0f} to {rect[1]:.0f} px on a 780 px screen")
        failures += error_failures(errors, "settings kn")
    finally:
        browser.close()

    # settings-4 (fresh audit): Save held Settings on screen, button disabled and no
    # word why, for as long as the remote health probe took (up to its 4 s timeout).
    browser, page, errors = open_flow(playwright)
    try:
        page.wait_for_function("() => typeof openSettings === 'function'")
        page.evaluate("""() => {
          window.__healthDone = 0;
          const realFetch = window.fetch;
          window.fetch = (input, init) => {
            const url = String(input && input.url || input);
            if (!url.includes('/v1/health')) return realFetch(input, init);
            return new Promise((resolve) => setTimeout(resolve, 2500))
              .then(() => realFetch(input, init))
              .finally(() => { window.__healthDone += 1; });
          };
          openSettings();
        }""")
        page.fill("#setName", "Quick Save")
        page.evaluate("() => document.getElementById('setSave').click()")
        page.wait_for_timeout(500)
        check(home_visible(page) and not settings_visible(page),
              "settings-4: Save still showed Settings 500 ms after the tap while the"
              " health probe was slow")
        page.wait_for_function("() => window.__healthDone > 0", timeout=10_000)
        check(page.evaluate("() => localStorage.getItem('sender_name')") == "Quick Save",
              "settings-4: Save did not persist the name")
        failures += error_failures(errors, "settings-4")
    finally:
        browser.close()

if failures:
    print("SETTINGS SAVE TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("SETTINGS SAVE TEST PASS")
