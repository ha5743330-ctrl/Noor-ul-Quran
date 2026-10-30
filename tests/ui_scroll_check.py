"""Playwright (host): long lists scroll in their own box and Load more works, at 360/390/768/1280.
API + Supabase stubbed with 120+ rows. Run: python tests/ui_scroll_check.py [--shots]"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).parent / "screenshots"
PORT = 8785
BASE = f"http://localhost:{PORT}"
N = 120
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'a1', email: 'a@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
USERS = [{"id": f"id{i}", "email": f"user{i}@example.com", "display_name": f"User {i}", "created_at": "2026-09-01T10:00:00Z",
          "last_sign_in_at": "2026-10-28T09:30:00Z", "status": "active", "reason": "", "premium_access": False, "is_admin": False,
          "avatar_url": None, "videos_today": 0, "storage_bytes": 0} for i in range(N)]
AUDIT = [{"at": "2026-10-29T10:00:00Z", "actor_email": "a@example.com", "target_email": f"user{i}@example.com", "action": "block", "reason": ""} for i in range(N)]
PICKS = [{"id": f"30u{i}", "bucket": 30, "mode": "urdu", "ref": f"2:{i}", "surah": 2, "surah_name": "Al-Baqara", "seconds": 20.0,
          "over_bucket": False, "selectable": True} for i in range(1, N)]
HISTORY = [{"id": f"h{i}", "type": "video", "filename": f"v{i}.mp4", "surah": 2, "start_ayah": 1, "end_ayah": 1, "has_file": True,
            "created_at": "2026-10-29T10:00:00Z"} for i in range(N)]
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def api(route):
    url = urlparse(route.request.url)
    query = parse_qs(url.query)
    path = re.sub(r"^.*?/api", "", url.path)
    body = {}
    if path == "/access":
        body = {"user_id": "a1", "email": "a@example.com", "is_admin": True, "premium_access": True, "daily_limit": None, "daily_remaining": None, "max_video_seconds": 35}
    elif path == "/me":
        body = {"id": "a1", "email": "a@example.com", "display_name": "A", "avatar_url": None, "is_admin": True, "status": "active"}
    elif path == "/admin/users":
        page = int(query["page"][0])
        body = {"users": USERS[(page - 1) * 50:page * 50], "total": N, "page": page, "per_page": 50, "stats": {"total": N, "premium": 0}}
    elif path == "/admin/audit":
        offset = int(query["offset"][0])
        body = {"entries": AUDIT[offset:offset + 50], "total": N}
    elif path == "/admin/backgrounds":
        body = [{"filename": f"clip{i}.mp4", "size": 1048576} for i in range(N)]
    elif path == "/admin/music":
        body = {"music_files": [f"song{i}.mp3" for i in range(N)]}
    elif path == "/picks":
        body = {"cap": 35, "picks": PICKS, "counts": {"total": len(PICKS)}}
    elif path == "/history":
        body = {"videos": HISTORY}
    elif path == "/reciters":
        body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"}, {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad"}],
                "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
    elif path == "/music-list":
        body = {"music_files": []}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def open_page(browser, width, height, url):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(STUB)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and not re.search(r"cdn\.|net::ERR|Failed to load resource", m.text) and page.errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/{url}", wait_until="networkidle")
    return page


def boxed(page, selector, height):
    """The element scrolls by itself and is no taller than ~60vh."""
    return page.evaluate("""([s, h]) => { const e = document.querySelector(s); const c = getComputedStyle(e);
        return c.overflowY === 'auto' && e.scrollHeight > e.clientHeight && e.clientHeight <= h * 0.61; }""", [selector, height])


def stays_sticky(page, box, head):
    """Scrolling the box leaves the header exactly where it was."""
    return page.evaluate("""([b, h]) => { const box = document.querySelector(b), head = document.querySelector(h);
        const before = head.getBoundingClientRect().top; box.scrollTop = 400;
        const after = head.getBoundingClientRect().top; box.scrollTop = 0; return Math.abs(before - after) < 1; }""", [box, head])


def screens(page, height):
    return page.evaluate("document.documentElement.scrollHeight") / height


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((360, 740), (390, 844), (768, 1024), (1280, 800)):
            tag = f"{width}px"

            page = open_page(browser, width, height, "admin-console.html")
            page.wait_for_selector(".au-item", state="attached")
            page.wait_for_selector("#audit-list li", state="attached")
            page.wait_for_selector("#ap-list .ap-row", state="attached")
            check(f"{tag}: admin page under ~3 screens by default", screens(page, height) <= 3, f"{screens(page, height):.1f}")
            page.evaluate("document.querySelectorAll('details[data-sec]').forEach(d => d.open = true)")
            for name, selector in (("Users", "#users-section .scroll-box"), ("Audit", "#audit-list"), ("Clips", "#background-list"), ("Music", "#music-list"), ("Picks", "#ap-list")):
                check(f"{tag}: admin {name} scrolls in its own box", boxed(page, selector, height))
            check(f"{tag}: Users search stays put", stays_sticky(page, "#users-section .scroll-box", "#user-search"))
            check(f"{tag}: no horizontal scroll (admin)", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            check(f"{tag}: Users starts at 50", page.locator(".au-item").count() == 50)
            page.click("#users-more")
            page.wait_for_function("document.querySelectorAll('.au-item').length === 100")
            page.click("#users-more")
            page.wait_for_function("document.querySelectorAll('.au-item').length === 120")
            check(f"{tag}: Users Load more ends at 120, no repeats", len(set(page.locator(".au-who small").all_inner_texts())) == 120 and page.locator("#users-more").is_hidden())
            page.click("#refresh-users")
            page.wait_for_timeout(300)
            check(f"{tag}: Users refresh keeps what was loaded", page.locator(".au-item").count() == 120)
            check(f"{tag}: Audit starts at 50", page.locator("#audit-list li").count() == 50)
            page.click("#audit-more")
            page.wait_for_function("document.querySelectorAll('#audit-list li').length === 100")
            page.click("#audit-more")
            page.wait_for_function("document.querySelectorAll('#audit-list li').length === 120")
            check(f"{tag}: Audit Load more ends at 120", page.locator("#audit-more").is_hidden())
            check(f"{tag}: no console errors (admin)", not page.errors, str(page.errors))
            if "--shots" in sys.argv:
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / f"scroll-admin-{width}.png"), full_page=True)
            page.context.close()

            page = open_page(browser, width, height, "library.html")
            page.wait_for_selector(".lib-item")
            check(f"{tag}: Library scrolls in its own box", boxed(page, "#lib-list", height))
            check(f"{tag}: Library page under ~3 screens", screens(page, height) <= 3, f"{screens(page, height):.1f}")
            check(f"{tag}: Library starts at 50", page.locator(".lib-item").count() == 50)
            page.click("#lib-more")
            check(f"{tag}: Library Load more adds 50", page.locator(".lib-item").count() == 100)
            page.click("#lib-more")
            check(f"{tag}: Library Load more ends at 120", page.locator(".lib-item").count() == 120 and page.locator("#lib-more").is_hidden())
            page.click('.lib-tabs [data-filter="video"]')
            check(f"{tag}: filter tab goes back to 50", page.locator(".lib-item").count() == 50)
            check(f"{tag}: no horizontal scroll (library)", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            check(f"{tag}: no console errors (library)", not page.errors, str(page.errors))
            if "--shots" in sys.argv:
                page.screenshot(path=str(SHOTS / f"scroll-library-{width}.png"), full_page=True)
            page.context.close()

            page = open_page(browser, width, height, "generate.html")
            page.wait_for_function("window.userAccess")
            page.click("#btn-picks")
            page.wait_for_selector(".pk-row")
            check(f"{tag}: Generate Picks scrolls in its own box", boxed(page, ".pk-box", height))
            check(f"{tag}: Picks search stays put", stays_sticky(page, ".pk-box", ".pk-search"))
            # The form itself is the long part of this page; what matters here is that the list no longer adds to it.
            check(f"{tag}: 119 picks add at most one box to the page", page.evaluate("document.querySelector('.pk-box').offsetHeight") <= height * 0.61)
            print(f"     {tag}: generate page {screens(page, height):.1f} screens")
            page.evaluate("""() => { const c = document.getElementById('picks-batch'); c.hidden = false;
                c.innerHTML = '<ul class="pk-batch-list">' + '<li class="pk-brow"><label class="pk-label">x</label></li>'.repeat(25) + '</ul>'; }""")
            check(f"{tag}: This-batch scrolls in its own box", boxed(page, ".pk-batch-list", height))
            check(f"{tag}: Recent box is capped", page.evaluate("getComputedStyle(document.getElementById('recent-videos-list')).overflowY") == "auto")
            check(f"{tag}: no horizontal scroll (generate)", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            page.click("#btn-random")
            check(f"{tag}: Random panel opens", page.locator("#mode-random").is_visible())
            page.click("#btn-pick")
            check(f"{tag}: Single panel opens", page.locator("#input-pick").is_visible())
            check(f"{tag}: no console errors (generate)", not page.errors, str(page.errors))
            if "--shots" in sys.argv:
                page.screenshot(path=str(SHOTS / f"scroll-generate-{width}.png"), full_page=True)
            page.context.close()
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
