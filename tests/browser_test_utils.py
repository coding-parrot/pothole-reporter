"""Shared browser-test setup that never puts credentials in URLs or server logs."""


def open_app(page, key):
    page.goto("http://localhost:8765/")
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("key => localStorage.setItem('openai_key', key)", key)
    page.reload()
    page.wait_for_load_state("networkidle")
