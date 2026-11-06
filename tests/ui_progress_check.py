"""Playwright (host): waiting card for video + ayah image, light+dark at 375/1280, stubbed job stages.
No console errors, no horizontal scroll. Run: python tests/ui_progress_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8792
BASE = f"http://localhost:{PORT}"
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
STUB = """window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}) }})};"""
ACCESS = {"user_id": "p1", "email": "p@example.com", "is_admin": False, "premium_access": True, "daily_limit": 30,
          "daily_remaining": 20, "max_video_seconds": 35, "max_video_seconds_photo": 35}
VERSES = {"112:1": ("قُلْ هُوَ ٱللَّهُ أَحَدٌ", "Say: He is Allah, One", "کہہ دو وہ اللہ ایک ہے"),
          "94:5": ("فَإِنَّ مَعَ ٱلْعُسْرِ يُسْرًا", "Lo! with hardship goeth ease", "بیشک مشکل کے ساتھ آسانی ہے"),
          "94:6": ("إِنَّ مَعَ ٱلْعُسْرِ يُسْرًا", "Lo! with hardship goeth ease", "بیشک مشکل کے ساتھ آسانی ہے")}
VIDEO = {"filename": "a.mp4", "id": "v1", "type": "video", "caption": "c"}
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def make_env(browser, theme, width, jobs, hold_image):
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    ctx.add_init_script(STUB)
    ctx.add_init_script(f"try{{localStorage.setItem('noor-theme','{theme}')}}catch(e){{}}")
    page = ctx.new_page()
    errors, held, polls = [], [], {"n": 0}
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and not re.search(r"Failed to load resource|net::ERR|ERR_", m.text) and errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))

    def api(route):
        url = route.request.url
        path = re.sub(r"^.*?/api", "", url.split("?")[0])
        out = {"/access": ACCESS, "/history": {"videos": []}, "/music-list": {"music_files": []}, "/me": {"is_admin": False}}.get(path, {})
        status = 200
        if path == "/search":
            ref = re.search(r"q=([\d:]+)", url)[1]
            a, e, u = VERSES.get(ref, VERSES["112:1"])
            out = {"results": [{"ref": ref, "arabic": a, "english": e, "urdu": u}]}
        elif path == "/reciters":
            out = {"reciters": [], "defaults": {}}
        elif path == "/generate":
            status, out = 202, {"job_id": "j1", "status": "queued", "videos": [], "items": [{"ref": "112:1", "status": "queued"}], "queue_ahead": 0, "estimated_wait_seconds": 20}
        elif path == "/generate/j1":
            out = jobs[min(polls["n"], len(jobs) - 1)]
            polls["n"] += 1
        elif path == "/generate-image":
            held.append(route)
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(out))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/generate.html", wait_until="domcontentloaded")
    page.wait_for_function("window.userAccess")
    return ctx, page, errors, held


def run(browser, theme, width):
    tag = f"[{theme} {width}]"
    item = {"ref": "112:1", "status": "queued"}
    jobs = [{"job_id": "j1", "status": "queued", "videos": [], "items": [item], "queue_ahead": 1, "estimated_wait_seconds": 10},
            {"job_id": "j1", "status": "processing", "videos": [], "items": [item], "estimated_wait_seconds": 5, "percent": 60, "stage": "frames", "eta_seconds": 12},
            {"job_id": "j1", "status": "completed", "videos": [VIDEO], "items": [{**item, "status": "done"}], "message": "ok"}]
    ctx, page, errors, held = make_env(browser, theme, width, jobs, False)
    page.fill("#input-pick", "112:1-4")
    page.click("#generate-button")
    page.wait_for_selector("#waiting-card:not([hidden]) .wc-w.on", timeout=8000)
    check(f"{tag} video: card shown, empty hidden", "hidden" in page.get_attribute("#empty-results", "class"))
    check(f"{tag} video: status is aria-live", page.get_attribute("#waiting-card .wc-status", "aria-live") == "polite" and page.get_attribute("#waiting-card .wc-status", "role") == "status")
    check(f"{tag} video: Quran font word reveal + Urdu translation", page.locator("#waiting-card .wc-ar.ar-text .wc-w").count() > 0 and page.get_attribute("#waiting-card .wc-tr", "lang") == "ur")
    page.wait_for_function("document.querySelectorAll('#waiting-card .wc-stages li.on').length === 3", timeout=8000)
    check(f"{tag} video: stage icons follow job stage (frames=3 lit)", "Frames" in page.inner_text("#waiting-card .wc-status"))
    check(f"{tag} video: server ETA shown", "~12s" in page.inner_text("#waiting-card .wc-eta"))
    page.wait_for_function("document.querySelector('#waiting-card .wc-vr').textContent === '94:5-6'", timeout=5000)
    check(f"{tag} verse rotation shows 94:5-6 label", True)
    page.screenshot(path=str(ROOT / "tests" / "output" / f"waiting-video-{theme}-{width}.png"))
    check(f"{tag} video: no h-scroll while waiting", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    page.wait_for_selector("#video-preview-box:not(.hidden)", timeout=10000)
    check(f"{tag} video: card hidden on finish, result shown", page.locator("#waiting-card").is_hidden())
    check(f"{tag} video: no console errors / h-scroll", not errors and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), str(errors[:2]))
    ctx.close()

    ctx, page, errors, held = make_env(browser, theme, width, jobs, True)
    page.click(".studio-tabs button[data-tab=image]")
    page.fill("#image-ayah", "112:1")
    page.select_option("#image-style", "night_sky")
    page.select_option("#image-translation", "en")
    page.click("#generate-image-button")
    page.wait_for_selector("#waiting-card .wc-peek:not([hidden])", timeout=5000)
    check(f"{tag} image: shimmer peek in chosen style + text", page.locator(".wc-peek--night_sky").count() == 1 and "Image ban rahi hai" in page.inner_text("#waiting-card .wc-peek"))
    page.wait_for_selector("#waiting-card .wc-w", timeout=3000)
    check(f"{tag} image: English translation", page.get_attribute("#waiting-card .wc-tr", "lang") == "en")
    page.screenshot(path=str(ROOT / "tests" / "output" / f"waiting-image-{theme}-{width}.png"))
    check(f"{tag} image: no h-scroll while waiting", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"image_data": "data:image/gif;base64,R0lGODlhAQABAAAAACw=", "filename": "x.png", "reference": "112:1"}))
    page.wait_for_selector("#image-result-box:not(.hidden)", timeout=5000)
    check(f"{tag} image: card hidden on finish", page.locator("#waiting-card").is_hidden())
    check(f"{tag} image: no console errors", not errors, str(errors[:2]))
    ctx.close()


def reduced(browser):
    ctx = browser.new_context(viewport={"width": 375, "height": 800}, reduced_motion="reduce")
    ctx.add_init_script(STUB)
    page = ctx.new_page()
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"results": []} if "search" in r.request.url else ACCESS)))
    page.goto(f"{BASE}/generate.html", wait_until="domcontentloaded")
    page.evaluate("document.getElementById('empty-results'); window.NoorWait.start({kind:'image', ref:'', lang:'en', style:'ivory'})")
    names = page.evaluate("[...document.querySelectorAll('.wc-wave b, .wc-peek')].map(e => getComputedStyle(e).animationName + '|' + getComputedStyle(e, '::after').animationName)")
    check("reduced-motion: no animations", all(n.split('|')[0] == "none" and n.split('|')[1] == "none" for n in names), str(names))
    ctx.close()


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    (ROOT / "tests" / "output").mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in ("light", "dark"):
            for width in (375, 1280):
                run(browser, theme, width)
        reduced(browser)
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failed" if failures else "\nall ui_progress checks passed")
sys.exit(1 if failures else 0)
