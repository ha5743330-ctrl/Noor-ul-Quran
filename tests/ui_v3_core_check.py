"""Playwright (host): range clamp + estimate background param, library error state, admin status, estimate contrast.
API + Supabase stubbed. Run: python tests/ui_v3_core_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8787
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
failures, estimates = [], []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def make_api(fail_all=False, admin=False):
    def api(route):
        path = re.sub(r"^.*?/api", "", route.request.url.split("?")[0])
        if fail_all:
            return route.fulfill(status=500, content_type="application/json", body='{"detail":"Server error"}')
        body = {}
        if path == "/access":
            body = {"user_id": "p1", "email": "p@example.com", "is_admin": admin, "premium_access": True, "daily_limit": 30, "daily_remaining": 27, "max_video_seconds": 35, "max_video_seconds_photo": 60}
        elif path == "/reciters":
            body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"}, {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad"}],
                    "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
        elif path == "/estimate":
            estimates.append(route.request.url)
            body = {"estimated_seconds": 4.0, "fits_up_to": 99, "tolerance_fits_up_to": 99, "within_tolerance": True, "total_count": 1, "range_seconds": 4.0}
        elif path == "/history":
            body = {"videos": []}
        elif path == "/music-list":
            body = {"music_files": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    return api


def open_page(browser, name, api):
    context = browser.new_context(viewport={"width": 375, "height": 800})
    context.add_init_script(STUB)
    page = context.new_page()
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/{name}", wait_until="load")
    return page


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = open_page(browser, "generate.html", make_api())
        page.wait_for_function("window.userAccess")
        page.fill("#estimate-end", "11")
        page.select_option("#estimate-surah", "93")
        page.fill("#estimate-end", "11")
        page.select_option("#estimate-surah", "108")
        check("surah change clamps end to 3", page.input_value("#estimate-end") == "3", page.input_value("#estimate-end"))
        check("max attrs set", page.get_attribute("#estimate-end", "max") == "3" and page.get_attribute("#estimate-start", "max") == "3")
        page.fill("#estimate-end", "99")
        check("typing past max clamps", page.input_value("#estimate-end") == "3")
        page.fill("#input-pick", "112:1-9")
        check("direct box clamps (112 has 4)", page.input_value("#estimate-end") == "4" and page.input_value("#input-pick") == "112:1-4", page.input_value("#input-pick"))
        page.wait_for_timeout(900)
        check("no estimate request with end > ayat", all("surah=108&start=1&end=11" not in u for u in estimates))
        check("estimate sends background", estimates and "background=video" in estimates[-1], str(estimates[-1:]))
        check("estimate text is dark green on light", page.evaluate("getComputedStyle(document.getElementById('duration-estimate-text')).color") == "rgb(22, 101, 52)", page.evaluate("document.getElementById('duration-estimate-text').className + ' ' + getComputedStyle(document.getElementById('duration-estimate-text')).color + ' ' + document.body.className"))
        lib = open_page(browser, "library.html", make_api(fail_all=True))
        lib.wait_for_timeout(800)
        check("library error hides empty state", lib.is_hidden("#lib-empty") and "Server error" in lib.inner_text("#lib-status"))
        lib2 = open_page(browser, "library.html", make_api())
        lib2.wait_for_timeout(800)
        check("library empty state shown without error", lib2.is_visible("#lib-empty"))
        ad = open_page(browser, "admin-console.html", make_api(fail_all=True))
        ad.wait_for_timeout(800)
        check("admin status not Online on API failure", ad.inner_text("#ad-status") == "Offline", ad.inner_text("#ad-status"))
        ad2 = open_page(browser, "admin-console.html", make_api(admin=True))
        ad2.wait_for_timeout(800)
        check("admin status Online when API answers /access", ad2.inner_text("#ad-status") == "Online")
        browser.close()
finally:
    server.terminate()
sys.exit(1 if failures else 0)
