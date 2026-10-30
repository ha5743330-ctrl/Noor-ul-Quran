"""Playwright (host): generate page accordions + bottom Generate bar at 360/390/768, plain cards at 1280.
API + Supabase stubbed. Run: python tests/ui_generate_mobile_check.py [--shots]"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).parent / "screenshots"
PORT = 8786
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
failures = []
posted = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def api(route):
    path = re.sub(r"^.*?/api", "", route.request.url.split("?")[0])
    body, status = {}, 200
    if path == "/access":
        body = {"user_id": "p1", "email": "p@example.com", "is_admin": False, "premium_access": True, "daily_limit": 30, "daily_remaining": 27, "max_video_seconds": 35}
    elif path == "/reciters":
        body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Mishary Rashid Alafasy"}, {"id": "husary", "language": "ar", "display_name": "Mahmoud Khalil Al-Husary"},
                             {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad Ali Khan (Urdu)"}],
                "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
    elif path == "/music-list":
        body = {"music_files": ["calm.mp3"]}
    elif path == "/history":
        body = {"videos": []}
    elif path == "/estimate":
        body = {"estimated_seconds": 4.0, "fits_up_to": 1, "tolerance_fits_up_to": 1, "within_tolerance": True, "total_count": 1, "range_seconds": 4.0}
    elif path == "/suggest-ranges":
        body = {"suggestions": []}
    elif path == "/generate" and route.request.method == "POST":
        posted.append(json.loads(route.request.post_data))
        body, status = {"job_id": "j", "batch_id": "b", "status": "queued", "videos": [], "items": [], "queue_ahead": 0, "estimated_wait_seconds": 1}, 202
    elif path.startswith("/generate/j"):
        body = {"job_id": "j", "batch_id": "b", "status": "failed", "videos": [], "items": [], "error": "stub"}
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_page(browser, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(STUB)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/generate.html", wait_until="networkidle")
    page.wait_for_function("window.userAccess")
    return page


def in_viewport(page, selector):
    return page.evaluate("""s => { const r = document.querySelector(s).getBoundingClientRect();
        return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight; }""", selector)


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((360, 740), (390, 844), (768, 1024)):
            tag = f"{width}px"
            page = open_page(browser, width, height)
            check(f"{tag}: Surah & ayah and Mode are open", page.locator("#input-pick").is_visible() and page.locator("#btn-pick").is_visible() and page.locator("#input-content-mode").is_visible())
            check(f"{tag}: Mode then Surah come first", page.evaluate("""() => { const y = (e) => e.getBoundingClientRect().top;
                return y(document.getElementById('content-heading')) < y(document.getElementById('input-pick'))
                    && y(document.getElementById('input-pick')) < y(document.getElementById('sum-reciter')); }"""))
            for sel in ("#input-reciter", "#input-max-dur", "#video-style-color", "#input-music"):
                check(f"{tag}: {sel} starts collapsed", not page.locator(sel).is_visible())
            check(f"{tag}: reciter summary", page.inner_text("#sum-reciter") == "Alafasy / Shamshad", page.inner_text("#sum-reciter"))
            check(f"{tag}: quality summary", page.inner_text("#sum-quality") == "30 s / Fast", page.inner_text("#sum-quality"))
            check(f"{tag}: style summary", page.inner_text("#sum-style") == "Default", page.inner_text("#sum-style"))
            check(f"{tag}: background summary", page.inner_text("#sum-background") == "Local / No music", page.inner_text("#sum-background"))
            cut = page.evaluate("[...document.querySelectorAll('.gen-val')].filter(e => e.scrollWidth > e.clientWidth).map(e => e.id)")
            check(f"{tag}: default summaries fit on one line", not cut, str(cut))
            check(f"{tag}: Generate + counter in view at the top", in_viewport(page, "#generate-button") and in_viewport(page, "#daily-remaining"))
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight / 2)")
            page.wait_for_timeout(600)
            check(f"{tag}: Generate + counter in view mid-page", in_viewport(page, "#generate-button") and in_viewport(page, "#daily-remaining"))
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(600)
            check(f"{tag}: bar does not cover the footer", page.evaluate("""() => document.querySelector('footer').getBoundingClientRect().bottom
                <= document.querySelector('.generate-submit-wrap').getBoundingClientRect().top + 1"""))
            page.evaluate("scrollTo(0, 0)")

            page.locator("details.gen-acc summary").nth(0).click()
            page.select_option("#input-reciter", "husary")
            check(f"{tag}: summary follows the reciter", page.inner_text("#sum-reciter") == "Al-Husary / Shamshad", page.inner_text("#sum-reciter"))
            page.locator("details.gen-acc summary").nth(1).click()
            page.select_option("#input-quality", "high")
            page.wait_for_timeout(50)
            check(f"{tag}: summary follows quality", page.inner_text("#sum-quality") == "30 s / High", page.inner_text("#sum-quality"))
            page.locator(".background-music-card summary").click()
            page.select_option("#input-music", "calm.mp3")
            page.wait_for_timeout(50)
            check(f"{tag}: summary follows music", page.inner_text("#sum-background") == "Local / calm.mp3", page.inner_text("#sum-background"))
            page.locator(".advanced-style-card summary").click()
            page.click("#apply-video-style")
            page.wait_for_timeout(50)
            check(f"{tag}: summary follows style", page.inner_text("#sum-style") == "1 custom", page.inner_text("#sum-style"))
            check(f"{tag}: no horizontal scroll, all open", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            small = page.evaluate("""[...document.querySelectorAll('#reel-generator-form summary')].filter(e => e.getBoundingClientRect().height < 43.5).length""")
            check(f"{tag}: headers are tap-sized", not small)

            posted.clear()
            page.click("#generate-button")
            page.wait_for_timeout(400)
            body = posted[-1] if posted else {}
            check(f"{tag}: Generate still sends collapsed-card values", body.get("reciter_id") == "husary" and body.get("quality") == "high" and body.get("music") == "calm.mp3" and len(body.get("styles", {})) == 1, str(body))
            page.select_option("#input-content-mode", "arabic_only")
            page.wait_for_timeout(50)
            check(f"{tag}: Arabic-only summary drops voice and music", page.inner_text("#sum-reciter") == "Al-Husary" and page.inner_text("#sum-background") == "Local / No music")
            check(f"{tag}: no console errors", not page.errors, str(page.errors))
            page.context.close()

            page = open_page(browser, width, height)
            print(f"     {tag}: {page.evaluate('document.documentElement.scrollHeight') / height:.1f} screens")
            if "--shots" in sys.argv:
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / f"generate-mobile-{width}.png"), full_page=False)
                page.screenshot(path=str(SHOTS / f"generate-mobile-{width}-full.png"), full_page=True)
            page.context.close()

        page = open_page(browser, 1280, 800)
        check("1280px: Reciter and Quality stay open", page.locator("#input-reciter").is_visible() and page.locator("#input-max-dur").is_visible())
        page.locator("details.gen-acc summary").nth(0).click()
        check("1280px: header click does not collapse", page.locator("#input-reciter").is_visible())
        check("1280px: Advanced and Background still collapsed", not page.locator("#video-style-color").is_visible() and not page.locator("#input-music").is_visible())
        check("1280px: no summary values shown", not page.locator("#sum-reciter").is_visible())
        check("1280px: bar not fixed", page.evaluate("getComputedStyle(document.querySelector('.generate-submit-wrap')).position") != "fixed")
        if "--shots" in sys.argv:
            page.screenshot(path=str(SHOTS / "generate-mobile-1280.png"), full_page=True)
        page.context.close()
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
