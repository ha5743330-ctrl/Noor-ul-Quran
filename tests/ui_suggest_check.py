"""Playwright: suggestion chips fill the range inputs / image input (API stubbed). Run: python tests/ui_suggest_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8779
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'u', email: 'a@b.co'}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};"
SUGGEST = {"suggestions": [
    {"target": 15, "origin": "start", "allowed": True, "reason": None, "start": 1, "end": 2, "seconds": 13, "label": "15 sec: 112:1-2 (13 s)"},
    {"target": 60, "origin": "start", "allowed": False, "reason": "Free server limit 30 s", "start": 1, "end": 4, "seconds": 50, "label": "60 sec: 112:1-4 (50 s)"},
]}
FEATURED = {"ayahs": [{"ref": f"94:{n}", "theme": "sabr/patience", "length": "short"} for n in range(1, 9)]
            + [{"ref": "112:1", "theme": "tawhid", "length": "short"}]}
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/supabase.js", lambda r: r.fulfill(body=STUB, content_type="application/javascript"))
        page.route("**/config.js", lambda r: r.fulfill(body=CONFIG, content_type="application/javascript"))
        page.route("**/api/suggest-ranges*", lambda r: r.fulfill(body=json.dumps(SUGGEST), content_type="application/json"))
        page.route("**/api/featured", lambda r: r.fulfill(body=json.dumps(FEATURED), content_type="application/json"))
        page.route("**/generate.html", lambda r: r.fulfill(
            body=re.sub(r'integrity="[^"]+"', "", (ROOT / "public" / "generate.html").read_text(encoding="utf-8")),
            content_type="text/html"))
        page.goto(f"{BASE}/generate.html", wait_until="domcontentloaded")

        page.wait_for_selector("#range-suggestions button")
        chips = page.locator("#range-suggestions button")
        check("range chips rendered", chips.count() == 2)
        check("capped chip is disabled, not hidden", chips.nth(1).is_disabled() and "Free server limit" in chips.nth(1).inner_text())
        chips.nth(0).click()
        check("range chip fills start/end/direct box",
              page.input_value("#estimate-start") == "1" and page.input_value("#estimate-end") == "2"
              and page.input_value("#input-pick").endswith(":1-2"))

        page.wait_for_selector("#image-theme option[value='tawhid']", state="attached")
        page.select_option("#image-theme", "sabr/patience")
        page.click("#image-suggest-button")
        refs = page.locator("#image-suggestions button").all_inner_texts()
        check("5 suggestions from chosen theme", len(refs) == 5 and all(r.startswith("94:") for r in refs), str(refs))
        page.locator("#image-suggestions button").nth(0).click()
        check("image chip fills Surah:Ayah input", page.input_value("#image-ayah") == refs[0])
        browser.close()
finally:
    server.terminate()
sys.exit(1 if failures else 0)
