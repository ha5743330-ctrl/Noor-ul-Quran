"""Playwright walk of FEATURE_CHECKLIST.md: both themes x 375/768/1280. API + Supabase stubbed, every API call is recorded
and asserted. Also screenshots every other page. Run (Docker): python tests/ui_redesign_walk.py
Screenshots -> tests/screenshots/walk/. Uploads/deletes hit the stub only (no real Supabase in CI)."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).parent / "screenshots" / "walk"
PORT = 8791
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'a1', email: 'admin@example.com', user_metadata: {}}}}, error: null}),
  getUser: async () => ({data: {user: {id: 'a1', email: 'admin@example.com', user_metadata: {}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {unsubscribe() {}}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
USERS = [{"id": f"id{i}", "email": f"user{i}@example.com", "display_name": f"User {i}", "created_at": "2026-09-01T10:00:00Z",
          "last_sign_in_at": "2026-10-28T09:30:00Z", "status": ["active", "blocked"][i % 2], "reason": "", "premium_access": i % 2 == 0,
          "is_admin": False, "avatar_url": None, "videos_today": 1, "storage_bytes": 1000} for i in range(4)]
STATE = {"safe": False, "done": False}
calls = []
failures = []
cell = ""


def check(name, ok, detail=""):
    tag = f"[{cell}] {name}"
    print(("OK   " if ok else "FAIL ") + tag + (f"  {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(tag)


def api(route):
    req = route.request
    path = re.sub(r"^.*?/api", "", req.url.split("?")[0])
    query = req.url.split("?")[1] if "?" in req.url else ""
    raw = req.post_data_buffer
    calls.append((req.method, path, raw.decode('utf-8', 'replace') if raw else None, query))
    body, status = {}, 200
    if path == "/access":
        body = {"user_id": "a1", "email": "admin@example.com", "is_admin": True, "premium_access": True, "daily_limit": None,
                "daily_remaining": None, "max_video_seconds": 120, "max_video_seconds_photo": 120}
    elif path == "/me":
        body = {"id": "a1", "email": "admin@example.com", "display_name": "Admin", "avatar_url": None, "is_admin": True, "status": "active"}
    elif path == "/reciters":
        body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Mishary Alafasy"}, {"id": "husary", "language": "ar", "display_name": "Al-Husary"},
                             {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad Ali Khan (Urdu)"}],
                "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
    elif path == "/music-list":
        body = {"music_files": ["calm.mp3"]}
    elif path == "/history":
        body = {"videos": []}
    elif path == "/estimate":
        body = {"estimated_seconds": 4.0, "fits_up_to": 1, "tolerance_fits_up_to": 1, "within_tolerance": True, "total_count": 1, "range_seconds": 4.0}
    elif path == "/suggest-ranges":
        body = {"suggestions": [{"target": 15, "origin": "start", "allowed": True, "reason": None, "start": 1, "end": 2, "seconds": 13, "label": "15 sec: 1:1-2 (13 s)"}]}
    elif path == "/featured":
        body = {"ayahs": [{"ref": "94:5", "theme": "sabr/patience", "length": "short"}]}
    elif path == "/generate-image":
        body = {"filename": "x.png", "reference": "94:5", "image_data": PNG}
    elif path == "/generate" and req.method == "POST":
        body, status = {"job_id": "j", "batch_id": "b", "status": "queued", "videos": [], "items": [], "queue_ahead": 0, "estimated_wait_seconds": 1}, 202
    elif path.startswith("/generate/j"):
        if STATE["done"]:
            vids = [{"filename": f"r{i}.mp4", "url": f"/api/video/r{i}.mp4?exp=9999999999&sig=x", "caption": f"Caption {i}", "duration": 14, "reference": "1:1-2"} for i in (1, 2)]
            body = {"job_id": "j", "batch_id": "b", "status": "completed", "videos": vids, "items": [], "message": "done"}
        else:
            body = {"job_id": "j", "batch_id": "b", "status": "failed", "videos": [], "items": [], "error": "stub"}
    elif path == "/picks":
        body = {"cap": 120, "counts": {"total": 2}, "picks": [{"id": f"30u{i}", "bucket": 30, "mode": "urdu", "ref": f"2:{i}-{i + 1}", "surah": 2, "surah_name": "Al-Baqara",
                                                              "seconds": 20.0 + i, "over_bucket": False, "selectable": True} for i in (1, 2)]}
    elif path == "/admin/users":
        body = {"users": USERS, "total": 4, "page": 1, "per_page": 50, "stats": {"total": 4, "premium": 2}}
    elif path == "/admin/audit":
        body = {"entries": [{"at": "2026-10-29T10:00:00Z", "actor_email": "admin@example.com", "target_email": "u@e.co", "action": "block", "reason": ""}]}
    elif path == "/admin/diagnostics":
        body = {"ffmpeg_version": "6", "raqm": True, "quran_json_present": True, "quran_ayat_count": 6236, "background_file_count": 1, "music_file_count": 1,
                "output_free_bytes": 5e9, "output_used_bytes": 1e6, "history_entry_count": 1, "supabase_url_set": True, "supabase_anon_key_set": True,
                "supabase_service_role_key_set": True, "video_url_secret_set": True,
                "state_safe_mode": {"history.json": 503} if STATE["safe"] else {}}
    elif path == "/admin/backgrounds" and req.method == "GET":
        body = [{"filename": "clip1.mp4", "size": 1000}]
    elif path == "/admin/music" and req.method == "GET":
        body = {"music_files": ["calm.mp3"]}
    elif path == "/admin/photos" and req.method == "GET":
        body = [{"filename": "p1.jpg", "size": 1000, "enabled": True, "thumb_url": "/api/admin/photos/p1.jpg/thumb"}]
    elif path == "/admin/analytics":
        body = {"days": [{"day": f"2026-11-0{d}", "views": 10 * d, "uniques": 4 * d, "signups": 1, "videos": d, "failures": 0, "avg_render_secs": 12} for d in range(1, 5)], "status": {"enabled": True, "buffered_views": 0}}
    elif path == "/admin/ask-usage":
        body = {"enabled": True, "global_limit": 100, "user_limit": 5, "key_set": True, "last_error": None, "days": [{"day": time.strftime("%Y-%m-%d", time.gmtime()), "n": 7}]}
    elif path == "/ask":
        body = {"status": "ok", "answer": "Stub answer.", "ayat": []}
    elif path == "/search":
        body = {"results": []}
    elif req.method in ("POST", "DELETE", "PATCH", "PUT"):
        body = {"ok": True}
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def new_page(browser, theme, width, user_admin=True):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme, reduced_motion="reduce")
    ctx.add_init_script(STUB)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and "cdn." not in m.text and page.errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    return page


def go(page, name, wait=None):
    page.goto(f"{BASE}/{name}", wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("load", timeout=8000)
    except Exception:
        pass
    if wait:
        page.wait_for_selector(wait, state="attached", timeout=8000)
    page.wait_for_timeout(500)


def clean(page, label):
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(f"{label}: no h-scroll", over <= 0, str(over))
    check(f"{label}: no console errors", not page.errors, str(page.errors[:2]))
    page.errors.clear()


def last(method, path):
    m = [c for c in calls if c[0] == method and c[1] == path]
    return json.loads(m[-1][2]) if m and m[-1][2] and m[-1][2].startswith("{") else (m[-1] if m else None)


def submit(page):
    calls.clear()
    page.click("#generate-button")
    page.wait_for_function("1", timeout=1000)
    for _ in range(30):
        if any(c[1] == "/generate" and c[0] == "POST" for c in calls):
            break
        page.wait_for_timeout(100)
    return last("POST", "/generate")


def walk_generate(browser, theme, width):
    page = new_page(browser, theme, width)
    go(page, "generate.html", "#generate-button")
    page.wait_for_function("window.userAccess")
    page.wait_for_selector("#input-reciter option[value=husary]", state="attached")
    clean(page, "generate load")
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    # Single (default) + estimate + chips
    page.fill("#input-pick", "1:1-2")
    page.dispatch_event("#input-pick", "input")
    page.wait_for_timeout(700)
    check("estimate requested + shown", any(c[1] == "/estimate" for c in calls) and "Hisaab ho raha" not in page.inner_text("#duration-estimate-text"), page.inner_text("#duration-estimate-text"))
    check("suggestion chips rendered", page.locator("#range-suggestions button").count() >= 1)
    body = submit(page)
    check("Single mode posts pick", isinstance(body, dict) and "1:1" in json.dumps(body), str(body))
    # 4 modes via tiles
    for cm in ("urdu_only", "full", "arabic_only", "arabic_urdu_text"):
        page.click(f'#mode-tiles [data-cm="{cm}"]')
        check(f"tile {cm} syncs select", page.input_value("#input-content-mode") == cm and page.get_attribute(f'#mode-tiles [data-cm="{cm}"]', "aria-pressed") == "true")
        body = submit(page)
        check(f"mode {cm} sent", isinstance(body, dict) and body.get("content_mode") == cm, str(body))
    # reciter, animation, layout, background type, music
    page.select_option("#input-reciter", "husary")
    page.select_option("#input-text-animation", "fade")
    page.select_option("#input-text-layout", "two_lines")
    page.select_option("#input-background-type", "photos")
    page.select_option("#input-music", "calm.mp3")
    page.click('#mode-tiles [data-cm="arabic_urdu_text"]')
    body = submit(page)
    s = json.dumps(body)
    check("reciter sent", '"husary"' in s, s)
    check("text_animation sent", body.get("text_animation") == "fade", s)
    check("text_layout sent", body.get("text_layout") == "two_lines", s)
    check("background photos sent", body.get("background_type") == "photos", s)
    check("music sent", "calm.mp3" in s, s)
    # quality + max duration sent
    page.select_option("#input-quality", "high")
    page.select_option("#input-max-dur", "60")
    body = submit(page)
    check("quality + max duration sent", body.get("quality") == "high" and "60" in json.dumps(body), json.dumps(body)[:200])
    # completed job -> preview, counter, download, ZIP, caption
    STATE["done"] = True
    page.click("#btn-pick")
    submit(page)
    page.wait_for_selector("#video-preview-box:not(.hidden)", timeout=8000)
    check("results: preview box + counter 1 / 2", "1 / 2" in page.inner_text("#preview-counter"), page.inner_text("#preview-counter"))
    check("results: caption shown", "Caption" in page.inner_text("#preview-caption"), page.inner_text("#preview-caption"))
    check("results: download link set", bool(page.get_attribute("#download-btn", "href")) and page.get_attribute("#download-btn", "href") != "#", str(page.get_attribute("#download-btn", "href")))
    check("results: ZIP button visible for 2 reels", page.is_visible("#download-all-btn"))
    page.click("#preview-next")
    check("results: next reel -> 2 / 2", "2 / 2" in page.inner_text("#preview-counter"), page.inner_text("#preview-counter"))
    check("results: Recent list + Library link present", page.is_visible("#recent-videos-card"))
    STATE["done"] = False
    # Picks
    page.click("#btn-picks")
    page.wait_for_function("document.querySelector('#picks-root').children.length > 0", timeout=5000)
    check("Picks panel lists picks", page.locator("#picks-root").inner_text().strip() != "")
    # Random
    page.click("#btn-random")
    check("Random panel visible", page.is_visible("#input-random-count"))
    page.fill("#input-random-count", "2")
    body = submit(page)
    check("Random mode posts", isinstance(body, dict) and (body.get("mode") == "random" or body.get("count") == 2 or "random" in json.dumps(body)), str(body))
    # Ayah Image tab: translation / style / label toggle
    page.click("#tab-image")
    check("Ayah Image tab hides reel panes", not page.is_visible("#generate-button") and page.is_visible("#generate-image-button"))
    page.select_option("#image-style", "night_sky")
    page.select_option("#image-translation", "ur")
    page.uncheck("#image-show-surah")
    calls.clear()
    page.click("#generate-image-button")
    page.wait_for_selector("#image-result-box:not(.hidden)", timeout=6000)
    b = last("POST", "/generate-image")
    check("image request has style/translation/show_surah", b == {"style": "night_sky", "translation": "ur", "show_surah": False} or (isinstance(b, dict) and b.get("style") == "night_sky" and b.get("translation") == "ur" and b.get("show_surah") is False), str(b))
    check("image shown + download link", page.is_visible("#generated-ayah-image") and page.get_attribute("#download-image-btn", "href").startswith("data:image"))
    page.click("#tab-reel")
    check("Library link in Recent", page.locator('a[href="/library.html"]').count() >= 1)
    clean(page, "generate end")
    page.context.close()


def walk_admin(browser, theme, width):
    STATE["safe"] = True
    page = new_page(browser, theme, width)
    go(page, "admin-console.html", ".au-item")
    check("safe-mode banner shown", page.is_visible("#state-warning") and "State not restored" in page.inner_text("#state-warning"))
    STATE["safe"] = False
    check("status pill Online", page.inner_text("#ad-status") == "Online", page.inner_text("#ad-status"))
    check("tab bar has 8 sections incl. Analytics/Settings", page.locator(".ad-jump a").count() == 8)
    page.evaluate("document.querySelectorAll('details[data-sec]').forEach(d => d.open = true)")
    page.wait_for_timeout(400)
    check("stat cards filled (videos/views/ask/disk)", all(page.inner_text(i).strip() not in ("-", "") for i in ("#stat-videos", "#stat-views", "#stat-ask", "#stat-disk")),
          str([page.inner_text(i) for i in ("#stat-videos", "#stat-views", "#stat-ask", "#stat-disk")]))
    check("analytics chart bars", page.locator("#analytics-chart rect").count() >= 4)
    check("Ask AI usage line", "Ask AI: on" in page.inner_text("#ask-usage"), page.inner_text("#ask-usage"))
    check("analytics table rows", page.locator("#analytics-table tbody tr").count() == 4)
    # block / unblock
    calls.clear()
    page.locator(".au-actions .btn-danger-outline", has_text="Block").first.click()
    page.wait_for_selector("dialog.confirm")
    page.fill("#block-reason", "spam")
    page.click("dialog.confirm .btn-danger")
    page.wait_for_timeout(500)
    check("block POST", any(c[0] == "POST" and c[1].endswith("/block") and "spam" in (c[2] or "") for c in calls), str(calls[-3:]))
    calls.clear()
    page.locator(".au-actions .btn", has_text="Unblock").first.click()
    page.wait_for_timeout(500)
    check("unblock POST", any(c[0] == "POST" and c[1].endswith("/unblock") for c in calls))
    # uploads + deletes (tiny files)
    for fid, form, path, name, data in (("background-file", "background-upload-form", "/admin/backgrounds", "t.mp4", b"\x00\x00\x00\x18ftypmp42"),
                                         ("photo-file", "photo-upload-form", "/admin/photos", "t.png", PNG.split(",")[1] and __import__("base64").b64decode(PNG.split(",")[1])),
                                         ("music-file", "music-upload-form", "/admin/music", "t.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00")):
        calls.clear()
        page.set_input_files(f"#{fid}", {"name": name, "mimeType": "application/octet-stream", "buffer": data})
        page.click(f"#{form} .btn-primary")
        page.wait_for_timeout(700)
        check(f"upload {name} sent", any(c[0] == "POST" and c[1].startswith(path) for c in calls), str(calls[-3:]))
    for lst, path in (("#background-list", "/admin/backgrounds"), ("#photo-list", "/admin/photos"), ("#music-list", "/admin/music")):
        calls.clear()
        btn = page.locator(f"{lst} .btn-danger-outline").first
        btn.click()
        page.wait_for_selector("dialog.confirm")
        page.click("dialog.confirm .btn-danger")
        page.wait_for_timeout(500)
        check(f"delete via {path}", any(c[0] == "DELETE" and c[1].startswith(path) for c in calls), str(calls[-3:]))
    check("picks manager rows", page.locator(".ap-row").count() >= 1)
    check("audit rows", page.locator("#audit-list li").count() >= 1)
    check("diagnostics grid", page.locator("#diagnostics-grid div").count() >= 8)
    clean(page, "admin")
    page.context.close()


def walk_pages(browser, theme, width):
    for name, sel in (("index.html", ".lp-hero"), ("library.html", None), ("profile.html", None), ("ask.html", None), ("search.html", None),
                      ("soon.html", None), ("privacy.html", None), ("terms.html", None), ("auth.html", None), ("generate.html", None), ("admin-console.html", None)):
        page = new_page(browser, theme, width)
        go(page, name, sel)
        clean(page, name)
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name.split('.')[0]}-{theme}-{width}.png"), full_page=True)
        page.context.close()
    # Ask AI / Search interaction
    page = new_page(browser, theme, width)
    go(page, "ask.html")
    calls.clear()
    page.fill("#ask-q", "What does the Quran say about patience?")
    page.click("#ask-go")
    page.wait_for_timeout(800)
    check("Ask AI posts /ask and shows answer", any(c[1] == "/ask" and c[0] == "POST" for c in calls) and "Stub answer" in page.inner_text("#ask-answer"), page.inner_text("#ask-answer")[:80])
    page.context.close()
    page = new_page(browser, theme, width)
    go(page, "search.html")
    page.locator("input").first.fill("baqara")
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    check("Search page responds", page.locator("body").inner_text() != "")
    page.context.close()
    # theme toggle
    page = new_page(browser, theme, width)
    go(page, "index.html", ".lp-hero")
    before = page.evaluate("document.documentElement.dataset.theme")
    btn = page.locator(".theme-toggle:visible").first
    if btn.count() == 0:
        page.click("[aria-label='Open menu'], .nav-burger, .nav-toggle")
        btn = page.locator(".theme-toggle:visible").first
    btn.click()
    after = page.evaluate("document.documentElement.dataset.theme")
    check("theme toggle flips + saves", before != after and page.evaluate("localStorage.getItem('noor-theme')") == after, f"{before}->{after}")
    page.context.close()


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in [a for a in sys.argv[1:] if not a.startswith("--")] or ("light", "dark"):
            for width in (375,) if "--quick" in sys.argv else (375, 768, 1280):
                cell = f"{theme}/{width}"
                for fn in (walk_generate, walk_admin, walk_pages):
                    try:
                        fn(browser, theme, width)
                    except Exception as exc:  # report and keep walking
                        import traceback; traceback.print_exc()
                        check(fn.__name__ + " completed", False, repr(exc)[:300])
        browser.close()
finally:
    server.terminate()
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
