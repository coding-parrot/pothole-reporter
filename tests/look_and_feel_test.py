# -*- coding: utf-8 -*-
"""A screen leads with the thing it is about, and explanations do not stand in its way.

The owner, of the app on his own phone (8 Oct 2026): "So much useless text without
describing main problem. Look at the map with a block of text above." Measured on a
360 px phone:
  - a report card gave its Email button half the card (the button's .send rule let it
    grow), so the place and time wrapped into a column about 100 px wide, four lines
    deep, and a card with no address showed nothing but chips;
  - a drive's heading ran the date and three counts together in one bold line;
  - the Pothole map screen put a card of notes and four tiles above the map, and the
    tiles sat three and one;
  - Settings stood four paragraphs of help between its controls, and Feedback opened
    with one.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


SEED = """
async () => {
  const db = await new Promise((resolve, reject) => {
    const open = indexedDB.open("potholes");
    open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error);
  });
  const now = Math.floor(Date.now() / 1000);
  const rows = [
    { status: "draft", decision: "accept", damage_type: "surface_breakup", size: "large",
      created_at: now - 3600, lat: 12.99717, lng: 77.62094, drive_id: "look-1",
      server_pothole_id: 11, address: null },
    { status: "draft", decision: "accept", damage_type: "pothole_cavity", size: "medium",
      created_at: now - 3500, lat: 12.9975, lng: 77.6212, drive_id: "look-1",
      server_pothole_id: 12,
      address: "Mosque Road Cross, Doddigunta, Cox Town, Bengaluru, 560005" },
  ];
  await new Promise((resolve, reject) => {
    const tx = db.transaction(["reports", "drives"], "readwrite");
    for (const row of rows) tx.objectStore("reports").add(row);
    tx.objectStore("drives").put({ id: "look-1", started_at: now - 3700, ended_at: now - 3300,
      checked: 212, found: 2, already: 0, captured: 212, dropped: 0 });
    tx.oncomplete = resolve; tx.onerror = () => reject(tx.error);
  });
  db.close();
  await loadReports();
}
"""

CARDS = """
() => [...document.querySelectorAll('#list .card.row')].map((card) => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
  const title = card.querySelector('.card-title');
  return { card: box(card), title: box(title), titleText: title ? title.textContent.trim() : null,
    titleCase: title ? getComputedStyle(title, '::first-letter').textTransform : null,
    email: box(card.querySelector('[data-quick-email]')),
    firstChip: (card.querySelector('.chip') || {}).className || null,
    text: card.innerText };
})
"""

with sync_playwright() as p:
    browser, page, errors = open_flow(p, storage={"feedback_nudged": "1"})
    try:
        page.set_viewport_size({"width": 360, "height": 800})
        page.wait_for_function("() => !document.getElementById('home').classList.contains('i18n-pending')")
        page.evaluate(SEED)
        page.wait_for_selector("#list .group-head")

        # ---------- the drive heading ----------
        head = page.evaluate("""() => {
          const head = document.querySelector('#list .group-head');
          const date = head.querySelector('.group-date');
          return { date: date ? date.textContent : null, all: head.innerText };
        }""")
        check(head["date"] is not None and "·" not in head["date"] and head["date"].strip() != "",
              f"a drive's heading does not give its date a line of its own: {head}")
        check("212" in head["all"], f"the drive heading lost its checked count: {head}")

        page.locator("#list .group-head").first.click()
        page.wait_for_selector("#list .card.row")
        cards = page.evaluate(CARDS)
        check(len(cards) == 2, f"expected two report cards, got {len(cards)}")
        by_title = {c["titleText"]: c for c in cards}

        # ---------- a card with an address leads with the place ----------
        placed = by_title.get("Mosque Road Cross, Doddigunta")
        check(placed is not None, f"no card is titled with its street and locality: {list(by_title)}")
        for card in cards:
            name = card["titleText"]
            width = card["title"]["width"] if card["title"] else 0
            check(width >= 200, f"{name}: the title has {width:.0f} px of a 360 px phone")
            check(card["card"]["height"] <= 170, f"{name}: the card is {card['card']['height']:.0f} px tall")
            check(card["email"] is not None, f"{name}: a confirmed draft has no Email button on its card")
            if card["email"] and card["title"]:
                check(card["email"]["width"] <= 190, f"{name}: the Email button is {card['email']['width']:.0f} px wide")
                check(card["email"]["height"] >= 44, f"{name}: the Email button is {card['email']['height']:.0f} px tall")
                check(card["email"]["top"] >= card["title"]["bottom"],
                      f"{name}: the Email button sits beside the title, not under it")
            check(card["firstChip"] and "draft" in card["firstChip"] or "synced" in (card["firstChip"] or ""),
                  f"{name}: the card's first chip is not its delivery state: {card['firstChip']}")
        if placed:
            check("pothole cavity" in placed["text"] and "medium" in placed["text"],
                  f"the placed card no longer says what was found: {placed['text']!r}")

        # ---------- a card with no address leads with the damage ----------
        bare = by_title.get("broken road surface")
        check(bare is not None, f"a report with no address is not titled by its damage: {list(by_title)}")
        if bare:
            check(bare["titleCase"] == "uppercase", "the damage title does not start with a capital")
            check("large" in bare["text"], f"the bare card lost its size: {bare['text']!r}")

        # ---------- the map screen: map first, then even tiles, then the notes ----------
        page.locator("#dashBtn").click()
        page.wait_for_function("() => document.querySelectorAll('#communityStats .card').length >= 2",
                               timeout=30_000)
        dash = page.evaluate("""() => {
          const top = (id) => document.getElementById(id).getBoundingClientRect();
          const tiles = [...document.querySelectorAll('#communityStats .card')].map((el) => Math.round(el.getBoundingClientRect().width));
          const own = [...document.querySelectorAll('#dashStats .card:not(.wide)')].map((el) => Math.round(el.getBoundingClientRect().width));
          return { back: top('dashBack').bottom, map: top('map').top, mapHeight: top('map').height,
                   stats: top('communityStats').top, note: top('communityNote').top, tiles, own,
                   refresh: top('dashRefresh').height, viewport: innerHeight };
        }""")
        check(dash["map"] - dash["back"] <= 24,
              f"{dash['map'] - dash['back']:.0f} px of other things stand between Back and the map")
        check(dash["map"] < dash["stats"] < dash["note"],
              f"the map screen is not map, counts, notes: {dash}")
        check(dash["mapHeight"] >= 0.45 * dash["viewport"], f"the map is {dash['mapHeight']:.0f} px tall")
        check(len(set(dash["tiles"])) == 1, f"the public tiles are not one width: {dash['tiles']}")
        check(len(set(dash["own"])) <= 1, f"your own tiles are not one width: {dash['own']}")
        check(dash["refresh"] >= 44, f"Refresh is {dash['refresh']:.0f} px tall")
        page.locator("#dashBack").click()

        # ---------- Settings and Feedback: help is folded, the disclaimer is not ----------
        page.locator("#gearBtn").click()
        page.wait_for_selector("#settings:not(.hidden)")
        folded = page.evaluate("""() => {
          // A closed <details> keeps its content laid out but unpainted, so the box is no evidence.
          const seen = (id) => { const el = document.getElementById(id); const fold = el && el.closest('details');
            return !!el && el.getClientRects().length > 0 && !(fold && !fold.open); };
          return { provider: seen('providerNote'), debug: seen('debugNote'), data: seen('settingsNote'),
                   independent: seen('independentNote'),
                   labels: [...document.querySelectorAll('#settings details.more summary')].map((el) => el.textContent),
                   word: t('more_info'),
                   targets: [...document.querySelectorAll('#settings details.more summary')].map((el) => el.getBoundingClientRect().height) };
        }""")
        check(not folded["provider"] and not folded["debug"] and not folded["data"],
              f"Settings still shows its help paragraphs unasked: {folded}")
        check(folded["independent"], "the unofficial-app notice is hidden in Settings")
        check(len(folded["labels"]) == 3 and set(folded["labels"]) == {folded["word"]},
              f"the Settings toggles are not labelled from the string table: {folded['labels']}")
        check(all(height >= 44 for height in folded["targets"]), f"a Details toggle is under 44 px: {folded['targets']}")
        if folded["labels"]:
            page.locator("#settings details.more summary").first.click()
            check(page.evaluate("() => document.getElementById('providerNote').closest('details').open "
                                "&& document.getElementById('providerNote').textContent.length > 40"),
                  "opening Details does not show the provider note")
        for lang, word in (("kn", "ವಿವರಗಳು"), ("mr", "तपशील"), ("bn", "বিস্তারিত")):
            page.evaluate("(lang) => { LANG = lang; applyLang(); }", lang)
            got = page.evaluate("() => (document.querySelector('#settings details.more summary') || {}).textContent")
            check(got == word, f"Details is {got!r} in {lang}")
        page.evaluate("() => { LANG = 'en'; applyLang(); }")
        page.locator("#feedbackBtn").click()
        page.wait_for_selector("#feedback:not(.hidden)")
        check(page.evaluate("() => { const fold = document.getElementById('feedbackIntro').closest('details'); "
                            "return !!fold && !fold.open; }"),
              "Feedback still opens with its paragraph showing")
        failures.extend(error_failures(errors, "look and feel"))
    finally:
        browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("LOOK AND FEEL TEST PASS")
