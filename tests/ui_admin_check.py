"""Playwright (host): admin console at phone/tablet/desktop widths. API + Supabase stubbed.
Screenshots -> tests/screenshots/admin-<width>-<card>.png. Run: python tests/ui_admin_check.py [--shots-only]"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).parent / "screenshots"
PORT = 8783
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'a1', email: 'admin@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
LONG = "a.very.long.email.address.for.testing.wrapping.behaviour@subdomain.example-company-name.international"
USERS = [
    {"id": f"id{i}", "email": LONG if i == 0 else f"user{i}@example.com", "display_name": "Muhammad Abdul Rahman Al-Farouq Ibn Sulaiman" if i == 0 else f"User {i}",
     "created_at": "2026-09-01T10:00:00Z", "last_sign_in_at": "2026-10-28T09:30:00Z", "status": ["active", "blocked", "deactivated"][i % 3],
     "reason": "Spam uploads and repeated abuse of the free tier limits" if i % 3 == 1 else "", "premium_access": i % 2 == 0,
     "is_admin": i == 3, "avatar_url": None, "videos_today": i % 5, "storage_bytes": i * 3_000_000}
    for i in range(6)
]
CLIPS = [{"filename": "calm-mountain-sunrise-timelapse-extended-version-final-v2-1080p.mp4", "size": 31_457_280}, {"filename": "clip2.mp4", "size": 1_048_576}]
MUSIC = {"music_files": ["abdul2025-nasheed-432919-with-a-really-long-descriptive-file-name.mp3.mpeg", "calm.mp3"]}
AUDIT = {"entries": [
    {"at": "2026-10-29T10:00:00Z", "actor_email": "admin@example.com", "target_email": LONG, "action": "block", "reason": "Spam"},
    {"at": "2026-10-28T10:00:00Z", "actor_email": "admin@example.com", "target_email": "user2@example.com", "action": "unblock", "reason": ""},
]}
DIAG = {"ffmpeg_version": "ffmpeg version 6.0", "raqm": True, "quran_json_present": True, "quran_ayat_count": 6236, "background_file_count": 2,
        "music_file_count": 2, "output_free_bytes": 5e9, "output_used_bytes": 1e6, "history_entry_count": 4, "supabase_url_set": True,
        "supabase_anon_key_set": True, "supabase_service_role_key_set": True, "video_url_secret_set": True}
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def api(route):
    path = re.sub(r"^.*?/api", "", route.request.url.split("?")[0])
    body = {}
    if path == "/access":
        body = {"user_id": "a1", "email": "admin@example.com", "is_admin": True, "premium_access": True, "daily_limit": None, "daily_remaining": None, "max_video_seconds": None}
    elif path == "/me":
        body = {"id": "a1", "email": "admin@example.com", "display_name": "Admin", "avatar_url": None, "is_admin": True, "status": "active"}
    elif path == "/admin/users":
        body = {"users": USERS, "total": 120, "page": 1, "per_page": 50, "stats": {"total": 120, "premium": 7}}
    elif path == "/admin/audit":
        body = AUDIT
    elif path == "/admin/diagnostics":
        body = DIAG
    elif path == "/admin/backgrounds":
        body = CLIPS
    elif path == "/admin/music":
        body = MUSIC
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def open_admin(browser, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(STUB)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "cdn." not in m.text and "net::ERR" not in m.text and page.errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/admin-console.html", wait_until="networkidle")
    page.wait_for_selector(".au-item")
    return page


def shoot(page, width, name, selector):
    SHOTS.mkdir(exist_ok=True)
    node = page.locator(selector).first
    if node.count():
        node.screenshot(path=str(SHOTS / f"admin-{width}-{name}.png"))


CARDS = {"top": ".ad-stats", "diagnostics": "#server-section", "users": "#users-section",
         "clips": "#clips-section", "music": "#music-section", "audit": "#audit-section"}

server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((360, 740), (390, 844), (768, 1024), (1280, 800)):
            page = open_admin(browser, width, height)
            page.wait_for_timeout(300)
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if "--shots-only" in sys.argv:
                print(width, "overflow", over, "offenders:", page.evaluate("""[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed').slice(0, 8).map(e => e.tagName + '.' + String(e.className).slice(0, 40))"""))
                page.screenshot(path=str(SHOTS / f"admin-{width}-default.png"), full_page=True)  # as a first-time visitor sees it
                page.evaluate("document.querySelectorAll('details[data-sec]').forEach(d => d.open = true)")
                page.wait_for_timeout(200)
                for name, sel in CARDS.items():
                    shoot(page, width, name, sel)
            else:
                if width <= 768:
                    check(f"{width}px: no horizontal scroll", over <= 0, str(over))
                check(f"{width}px: no console errors", not page.errors, str(page.errors))
                if width <= 820:
                    state = page.evaluate("[...document.querySelectorAll('details[data-sec]')].map(d => d.dataset.sec + ':' + d.open).join()")
                    check(f"{width}px: users open, others collapsed", state == "users:true,clips:false,music:false,audit:false,server:false", state)
                    page.click('.ad-jump a[href="#clips-section"]')
                    page.wait_for_timeout(2500)  # html has smooth scrolling
                    check(f"{width}px: chip opens + scrolls to section", page.evaluate("document.querySelector('#clips-section details').open && Math.abs(document.querySelector('#clips-section').getBoundingClientRect().top - 128) < 40 || document.documentElement.scrollTop + innerHeight >= document.documentElement.scrollHeight - 2"))
                    page.reload(wait_until="networkidle")
                    page.wait_for_selector(".au-item")
                    check(f"{width}px: open state remembered", page.evaluate("document.querySelector('#clips-section details').open"))
                    page.click('.ad-chips [data-status="blocked"]')
                    page.wait_for_timeout(300)
                    check(f"{width}px: status chip pressed", page.get_attribute('.ad-chips [data-status="blocked"]', "aria-pressed") == "true")
                    page.click(".au-actions .btn-danger-outline >> nth=0")
                    page.wait_for_selector("dialog.confirm")
                    box = page.locator("dialog.confirm").bounding_box()
                    check(f"{width}px: confirm is a full-width bottom sheet", box["width"] >= width - 1 and abs(box["y"] + box["height"] - height) < 2, str(box))
                    page.keyboard.press("Escape")
                small = page.evaluate("""[...document.querySelectorAll('main button, main a, main input:not([type=checkbox]):not([type=file]), main select')].filter(e => e.offsetParent && !e.closest('[hidden]'))
                    .filter(e => { const b = e.getBoundingClientRect(); return b.height < 43.5 }).map(e => (e.id || e.className || e.tagName).toString().slice(0, 30))""")
                if width <= 820:
                    check(f"{width}px: tap targets >= 44px", not small, str(small[:6]))
                    fs = page.evaluate("[...document.querySelectorAll('main input:not([type=checkbox]):not([type=file]), main select, main textarea')].filter(e => e.offsetParent && getComputedStyle(e).fontSize < '16px' && parseFloat(getComputedStyle(e).fontSize) < 16).map(e => e.id || e.type)")
                    check(f"{width}px: inputs >= 16px", not fs, str(fs))
            page.context.close()
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
