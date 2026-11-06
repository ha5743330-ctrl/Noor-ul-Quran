"""Playwright (host): walks every admin view (hash routes) in light + dark at 375/768/1280 with a stateful stubbed API.
Checks no console errors / h-scroll, plus block/unblock, upload/delete per media type, picks, analytics, Ask AI, safe mode.
Screenshots -> tests/screenshots/admin-<theme>-<width>-<view>.png (--shots). Run: python tests/ui_admin_check.py [--shots]"""
import datetime
import json
import os
import tempfile
import re
import subprocess
import sys
import time
from urllib.parse import unquote
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


TODAY = datetime.date.today().isoformat()
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f00000030101001842d9650000000049454e44ae426082")
VIEWS = ["dashboard", "users", "clips", "photos", "music", "picks", "analytics", "askai", "system"]
LOG = []  # (method, path) of every mutating call
MEDIA = {"/admin/backgrounds": "clips", "/admin/music": "music", "/admin/photos": "photos"}


def fresh_state():
    return {
        "users": [dict(u) for u in USERS], "clips": [dict(c) for c in CLIPS], "music": list(MUSIC["music_files"]),
        "photos": [{"filename": f"photo{i}.jpg", "size": 200_000 + i, "enabled": i != 2, "thumb_url": f"/api/admin/photos/photo{i}.jpg/thumb"} for i in range(3)],
    }


STATE = fresh_state()


def api(route):
    req = route.request
    path = re.sub(r"^.*?/api", "", req.url.split("?")[0])
    query = req.url.split("?", 1)[1] if "?" in req.url else ""
    if "thumb" in path:
        return route.fulfill(status=200, content_type="image/png", body=PNG)
    if req.method != "GET":
        LOG.append((req.method, path))
    body = {}
    m = re.fullmatch(r"/admin/users/(\w+)/(block|unblock)", path)
    if path == "/access":
        body = {"user_id": "a1", "email": "admin@example.com", "is_admin": True, "premium_access": True, "daily_limit": None, "daily_remaining": None, "max_video_seconds": None}
    elif path == "/me":
        body = {"id": "a1", "email": "admin@example.com", "display_name": "Admin", "avatar_url": None, "is_admin": True, "status": "active"}
    elif m:
        for u in STATE["users"]:
            if u["id"] == m.group(1):
                u["status"] = "blocked" if m.group(2) == "block" else "active"
    elif path == "/admin/users":
        q = re.search(r"q=([^&]*)", query)
        st = re.search(r"status=(\w+)", query)
        rows = [u for u in STATE["users"] if (not q or not q.group(1) or q.group(1) in u["email"]) and (not st or st.group(1) == "all" or u["status"] == st.group(1))]
        body = {"users": rows, "total": len(rows), "page": 1, "per_page": 50, "stats": {"total": 120, "premium": 7}}
    elif path == "/admin/audit":
        body = AUDIT
    elif path == "/admin/diagnostics":
        body = {**DIAG, "output_free_bytes": 1e8, "output_used_bytes": 9e8, "supabase_anon_key_set": False, "state_safe_mode": {"users.json": 503}}
    elif path in MEDIA:
        key = MEDIA[path]
        if req.method == "POST":
            name = unquote(re.search(r"filename=([^&]*)", query).group(1))
            if key == "clips":
                STATE[key].append({"filename": name, "size": 2_000_000})
            elif key == "music":
                STATE[key].append(name)
            else:
                STATE[key].append({"filename": name, "size": 100_000, "enabled": True, "thumb_url": f"/api/admin/photos/{name}/thumb"})
            body = {"ok": True}
        else:
            body = {"music_files": STATE["music"]} if key == "music" else STATE[key]
    elif path.startswith(("/admin/backgrounds/", "/admin/music/", "/admin/photos/")):
        parts = path.split("/")
        key = MEDIA["/" + "/".join(parts[1:3])]
        name = unquote(parts[3])
        if req.method == "DELETE":
            STATE[key] = [x for x in STATE[key] if (x if key == "music" else x["filename"]) != name]
        elif req.method == "PATCH":
            for x in STATE[key]:
                if x["filename"] == name:
                    x["enabled"] = not x["enabled"]
    elif path == "/admin/analytics":
        days = [{"day": (datetime.date.today() - datetime.timedelta(days=i)).isoformat(), "views": 40 + i * 3, "uniques": 20 + i, "signups": i % 3, "videos": 12 - i % 5,
                 "failures": 3 if i == 0 else 0, "avg_render_secs": 11.5, } for i in range(30)]
        body = {"days": days, "status": {"last_flush": "now", "buffered_views": 2, "last_error": None, "enabled": True},
                "failed_videos": [{"at": f"{TODAY}T09:00:00Z", "reason": "ffmpeg: out of memory while rendering clip"}]}
    elif path == "/admin/ask-usage":
        body = {"enabled": True, "global_limit": 100, "user_limit": 5, "key_set": True, "last_error": None, "days": [{"day": TODAY, "n": 85}, {"day": "2026-01-01", "n": 4}]}
    elif path == "/picks":
        body = {"cap": 35, "picks": [{"id": f"30u{i}", "bucket": 30, "mode": "urdu", "ref": f"2:{i}-{i + 2}", "surah_name": "Al-Baqara",
                                      "seconds": 20.0 + 9 * i, "over_bucket": i > 1, "selectable": i < 3} for i in range(1, 5)]}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def open_admin(browser, width, height, theme, hash_=""):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script(STUB)
    context.add_init_script(f"try{{localStorage.setItem('noor-theme','{theme}')}}catch(e){{}}")
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "cdn." not in m.text and "net::ERR" not in m.text and page.errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/admin-console.html{hash_}", wait_until="networkidle")
    page.wait_for_selector(".au-item", state="attached")
    return page


def go(page, view):
    page.evaluate(f"location.hash = '#/{view}'")
    page.wait_for_function(f"document.querySelector('.ad-view[data-view={view}]') && !document.querySelector('.ad-view[data-view={view}]').hidden")


def confirm(page, label):
    page.wait_for_selector("dialog.confirm")
    page.click(f"dialog.confirm button:has-text('{label}')")
    page.wait_for_selector("dialog.confirm", state="detached")


def functional(page, width, theme):
    tag = f"{theme} {width}px"
    wide = width > 1024
    # --- shell: sidebar vs drawer, active item, deep link, back button
    check(f"{tag}: sidebar {'visible' if wide else 'hidden until burger'}", page.is_visible("#ad-side") == wide)
    check(f"{tag}: burger {'hidden' if wide else 'shown'}", page.is_visible("#ad-burger") != wide)
    if not wide:
        page.click("#ad-burger")
        page.wait_for_selector("#ad-side", state="visible")
        check(f"{tag}: drawer links are 44px tall", page.evaluate("[...document.querySelectorAll('.ad-link')].every(a => a.getBoundingClientRect().height >= 43.5)"))
        page.click('.ad-link[data-nav="music"]')
        page.wait_for_selector("#ad-side", state="hidden")
    else:
        page.click('.ad-link[data-nav="music"]')
    page.wait_for_selector('.ad-view[data-view="music"]', state="visible")
    check(f"{tag}: nav click routes + highlights", page.get_attribute('.ad-link[data-nav="music"]', "aria-current") == "page" and page.is_visible('.ad-view[data-view="music"]'))
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#music-list .ad-media")
    check(f"{tag}: refresh keeps #/music", page.is_visible('.ad-view[data-view="music"]') and page.inner_text("#ad-title") == "Music")
    go(page, "users")
    page.go_back()
    page.wait_for_function("!document.querySelector('.ad-view[data-view=music]').hidden")
    check(f"{tag}: back button returns to previous view", True)

    # --- dashboard: attention card, failed list, safe-mode banner
    go(page, "dashboard")
    attn = page.inner_text("#attn-list")
    check(f"{tag}: Dhyan talab lists failed/storage/safe-mode/Ask AI", all(t in attn for t in ("3 videos fail", "Storage 90%", "State restore", "Ask AI limit")), attn)
    check(f"{tag}: failed videos card shows saved reason", "out of memory" in page.inner_text("#dash-failed"))
    check(f"{tag}: red safe-mode banner visible", page.is_visible("#state-warning") and "State not restored" in page.inner_text("#state-warning"))
    check(f"{tag}: 14-day chart drawn", page.locator("#dash-chart rect").count() == 28)
    check(f"{tag}: status pill online", page.inner_text("#ad-pill") == "API Online" and page.inner_text("#ad-status") == "Online")

    # --- users: search, filter, sort, block/unblock
    go(page, "users")
    page.fill("#user-search", "user2")
    page.wait_for_function("document.querySelectorAll('#users-list .au-item').length === 1")
    page.fill("#user-search", "")
    page.wait_for_function("document.querySelectorAll('#users-list .au-item').length === 6")
    page.click('#users-section [data-status="blocked"]')
    page.wait_for_function("document.querySelectorAll('#users-list .au-item').length === 2")
    check(f"{tag}: Blocked filter", page.get_attribute('#users-section [data-status="blocked"]', "aria-pressed") == "true")
    page.click('#users-section [data-status="all"]')
    page.wait_for_function("document.querySelectorAll('#users-list .au-item').length === 6")
    page.select_option("#user-sort", "videos")
    top = page.evaluate("[...document.querySelectorAll('#users-list .au-facts')].map(f => +f.children[2].querySelector('dd').textContent)")
    check(f"{tag}: sort by videos today", top == sorted(top, reverse=True), str(top))
    page.click("#users-list .au-actions .btn-danger-outline:has-text('Block') >> nth=0")
    page.wait_for_selector("dialog.confirm")
    page.fill("#block-reason", "spam")
    page.click("dialog.confirm button:has-text('Block')")
    page.wait_for_selector("dialog.confirm", state="detached")
    page.wait_for_function("document.querySelectorAll('#users-list .au-status.bad').length === 3")
    check(f"{tag}: block user", any(c[1].endswith("/block") for c in LOG))
    page.click("#users-list .au-actions .btn:has-text('Unblock') >> nth=0")
    page.wait_for_function("document.querySelectorAll('#users-list .au-status.bad').length === 2")
    check(f"{tag}: unblock user", any(c[1].endswith("/unblock") for c in LOG))
    check(f"{tag}: users list is a scroll container", page.evaluate("getComputedStyle(document.querySelector('#users-section .scroll-box')).overflowY") == "auto")

    # --- media: identical layout, validation, upload, search, delete-with-confirm
    for view, prefix, good, mime, bad in (("clips", "background", "new-clip.mp4", "video/mp4", "notes.txt"), ("photos", "photo", "new-photo.jpg", "image/jpeg", "doc.pdf"), ("music", "music", "new-song.mp3", "audio/mpeg", "pic.png")):
        go(page, view)
        check(f"{tag}: {view} has upload card + library", all(page.locator(f"#{view}-section {s}").count() for s in (".ad-drop", ".ad-limits", "progress", ".ad-media-grid", "input[type=search]")))
        if width >= 821:
            up, lib = page.locator(f"#{view}-section .ad-upload").bounding_box(), page.locator(f"#{view}-section .ad-lib").bounding_box()
            check(f"{tag}: {view} upload left of library", up["x"] < lib["x"] and abs(up["y"] - lib["y"]) < 4, f"{up} {lib}")
        page.set_input_files(f"#{prefix}-file", {"name": bad, "mimeType": "application/octet-stream", "buffer": b"x" * 100})
        check(f"{tag}: {view} rejects wrong type", "are allowed" in page.inner_text(f"#{prefix}-error"), page.inner_text(f"#{prefix}-error"))
        before = len(LOG)
        page.click(f"#{prefix}-upload-button")
        page.wait_for_timeout(150)
        check(f"{tag}: {view} blocks invalid upload", len(LOG) == before)
        page.set_input_files(f"#{prefix}-file", {"name": good, "mimeType": mime, "buffer": b"x" * 100})
        page.click(f"#{prefix}-upload-button")
        page.wait_for_function(f"[...document.querySelectorAll('#{view}-section .ad-media strong')].some(e => e.textContent === {json.dumps(good)})")
        check(f"{tag}: {view} upload appears in library", any(c[0] == "POST" for c in LOG[before:]))
        page.fill(f"#{prefix}-search", good[:5])
        page.wait_for_function(f"document.querySelectorAll('#{view}-section .ad-media').length === 1")
        page.fill(f"#{prefix}-search", "zzz-nomatch")
        page.wait_for_function(f"!document.querySelector('#{view}-section .ad-media') && document.querySelector('#{view}-section .st-empty')")
        page.fill(f"#{prefix}-search", good[:5])
        page.click(f"#{view}-section .ad-media .btn-danger-outline")
        confirm(page, "Cancel")
        check(f"{tag}: {view} delete confirm can be cancelled", page.locator(f"#{view}-section .ad-media").count() == 1)
        page.click(f"#{view}-section .ad-media .btn-danger-outline")
        confirm(page, "Delete")
        page.wait_for_function(f"!document.querySelector('#{view}-section .ad-media')")
        check(f"{tag}: {view} delete", any(c[0] == "DELETE" for c in LOG))
        page.fill(f"#{prefix}-search", "")
    go(page, "clips")
    big = Path(tempfile.gettempdir()) / "noor-big-clip.mp4"
    with big.open("wb") as f:
        f.truncate(51 * 1024 * 1024)
    page.set_input_files("#background-file", str(big))
    check(f"{tag}: clips rejects >50 MB", "50 MB limit" in page.inner_text("#background-error"), page.inner_text("#background-error"))
    big.unlink()
    go(page, "photos")
    page.click("#photo-list .ad-media-actions .btn-outline >> nth=0")
    page.wait_for_function("document.querySelectorAll('#photo-list .ad-media').length === 3")
    check(f"{tag}: photo enable/disable still works", any(c[0] == "PATCH" for c in LOG))

    # --- picks, analytics, ask ai, system
    go(page, "picks")
    page.wait_for_selector(".ap-row")
    check(f"{tag}: picks rows + counts", "over the 30 s bucket" in page.inner_text("#ap-counts"))
    go(page, "analytics")
    page.wait_for_selector("#analytics-table tbody tr")
    check(f"{tag}: analytics chart + table", page.locator("#analytics-chart rect").count() == 60 and page.locator("#analytics-table tbody tr").count() == 30)
    go(page, "askai")
    check(f"{tag}: Ask AI usage cards", "85 / 100" in page.inner_text("#ask-cards") and page.locator("#ask-days li").count() == 2)
    go(page, "system")
    pills = page.inner_text("#diagnostics-grid")
    check(f"{tag}: key pills Set / Not set", "Not set" in pills and pills.count("Set") >= 3, pills)
    check(f"{tag}: Supabase status + 7-day note", "Supabase OK" in page.inner_text("#sys-supabase") and "7 days" in page.inner_text("#sys-activity") and "Last activity" in page.inner_text("#sys-activity"))


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        SHOTS.mkdir(exist_ok=True)
        for theme in ("light", "dark"):
            for width, height in ((375, 800), (768, 1024), (1280, 800)):
                STATE.update(fresh_state())
                LOG.clear()
                page = open_admin(browser, width, height, theme)
                check(f"{theme} {width}px: theme applied", page.evaluate("document.documentElement.dataset.theme") == theme)
                for view in VIEWS:
                    go(page, view)
                    page.wait_for_timeout(120)
                    visible = page.evaluate("[...document.querySelectorAll('.ad-view')].filter(v => !v.hidden).map(v => v.dataset.view)")
                    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                    check(f"{theme} {width}px #{view}: only that view shown, no h-scroll", visible == [view] and over <= 0, f"{visible} over={over}")
                    if "--shots" in sys.argv:
                        page.screenshot(path=str(SHOTS / f"admin-{theme}-{width}-{view}.png"), full_page=True)
                if (theme, width) in (("light", 375), ("dark", 768), ("dark", 1280)):
                    functional(page, width, theme)
                check(f"{theme} {width}px: no console errors", not page.errors, str(page.errors[:3]))
                page.context.close()
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
