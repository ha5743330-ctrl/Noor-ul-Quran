"""Playwright (host): Ask page + Coming soon at 375/768/1280, states ok/refused/limit/error, no h-scroll.
API + Supabase stubbed. Run: python tests/ui_ask_check.py"""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8788
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
AYAH = {"ref": "2:153", "surah_name": "Al-Baqarah", "arabic": "يا أيها الذين آمنوا", "urdu": "اے ایمان والو", "english": "O ye who believe, seek help in patience.", "generate_url": "/generate.html?pick=2:153-153"}
RESP = {
    "ok": {"status": "ok", "answer": "Seek help through patience [2:153].", "cited": ["2:153"], "ayat": [AYAH], "message": ""},
    "refused": {"status": "refused", "answer": "", "cited": [], "ayat": [AYAH], "message": "AI does not answer rulings. Consult a qualified scholar."},
    "limit": {"status": "limit", "answer": "", "cited": [], "ayat": [AYAH], "message": "Daily limit reached, try tomorrow."},
}
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def open_page(browser, name, width, mode="ok", signed_in=True):
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    ctx.add_init_script(STUB.replace("session: {access_token", "session: null && {access_token") if not signed_in else STUB)
    page = ctx.new_page()
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))

    def api(route):
        path = route.request.url.split("/api", 1)[1].split("?")[0]
        if path == "/ask":
            if mode == "error":
                return route.fulfill(status=500, content_type="application/json", body="{}")
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(RESP[mode]))
        route.fulfill(status=200, content_type="application/json", body="{}")
    page.route("**/api/**", api)
    page.goto(f"{BASE}/{name}", wait_until="load")
    return page


def no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in (375, 768, 1280):
            pg = open_page(browser, "ask.html", w)
            pg.click(".ask-chips button")
            pg.wait_for_selector("#ask-answer a.ask-cite")
            check(f"{w} citation is a link to the ayah card", pg.get_attribute("a.ask-cite", "href") == "#ayah-2-153" and pg.is_visible("#ayah-2-153"))
            check(f"{w} Make video button", pg.inner_text("#ayah-2-153 .btn") == "Make video" and "pick=2:153-153" in pg.get_attribute("#ayah-2-153 .btn", "href"))
            check(f"{w} disclaimer", "not a fatwa" in pg.inner_text(".ask-disclaimer"))
            check(f"{w} no horizontal scroll", no_hscroll(pg))
            if w == 375:
                pg.screenshot(path=str(ROOT / "tests" / "screenshots" / "ask-375.png"), full_page=True)
            soon = open_page(browser, "soon.html", w)
            check(f"{w} soon lists 5 items", soon.locator(".sr-item").count() == 5 and no_hscroll(soon))
        for mode, text in (("refused", "qualified scholar"), ("limit", "Daily limit reached"), ("error", "Something went wrong")):
            pg = open_page(browser, "ask.html", 375, mode)
            pg.fill("#ask-q", "anything here")
            pg.press("#ask-q", "Enter")
            pg.wait_for_function("document.getElementById('ask-status').dataset.kind !== 'loading'")
            check(f"state {mode}", text in pg.inner_text("#ask-status") and pg.is_hidden("#ask-answer"))
        pg = open_page(browser, "ask.html", 375, signed_in=False)
        pg.fill("#ask-q", "anything here")
        pg.press("#ask-q", "Enter")
        pg.wait_for_selector("#ask-status a")
        check("signed out asks to sign in", "sign in" in pg.inner_text("#ask-status"))
        browser.close()
finally:
    server.terminate()
sys.exit(1 if failures else 0)
