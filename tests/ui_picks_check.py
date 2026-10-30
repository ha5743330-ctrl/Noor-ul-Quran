"""Playwright (host): Picks panel + "This batch" card on the generate page at 360/390/768/1280.
API + Supabase stubbed (nothing reaches a real project). Run: python tests/ui_picks_check.py [--shots]"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(__file__).parent / "screenshots"
PORT = 8784
BASE = f"http://localhost:{PORT}"
CAP = 35
DEFAULT = json.loads((ROOT / "data" / "picks_default.json").read_text(encoding="utf-8"))
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
failures = []
calls = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def view(pick):
    return {**pick, "surah": int(pick["ref"].split(":")[0]), "surah_name": f"Surah {pick['ref'].split(':')[0]}",
            "selectable": pick["seconds"] <= CAP, "over_bucket": pick["seconds"] > pick["bucket"]}


def api(route):
    request = route.request
    path = re.sub(r"^.*?/api", "", request.url.split("?")[0])
    query = request.url.split("?")[1] if "?" in request.url else ""
    calls.append((request.method, path, request.post_data))
    body, status = {}, 200
    if path == "/access":
        body = {"user_id": "p1", "email": "p@example.com", "is_admin": False, "premium_access": True,
                "daily_limit": 30, "daily_remaining": 30, "max_video_seconds": CAP}
    elif path == "/picks":
        bucket = int(re.search(r"bucket=(\d+)", query)[1])
        mode = re.search(r"mode=(\w+)", query)[1]
        rows = [view(p) for p in DEFAULT if p["bucket"] == bucket and p["mode"] == mode]
        body = {"picks": rows, "cap": CAP, "counts": {"total": len(rows)}}
    elif path == "/generate" and request.method == "POST":
        ids = json.loads(request.post_data)["pick_ids"]
        body = {"job_id": "job1", "batch_id": "b1", "status": "queued", "videos": [], "queue_ahead": 0, "estimated_wait_seconds": 60,
                "items": [{"index": i + 1, "pick_id": pid, "ref": pid, "status": "queued", "reason": None, "history_id": None} for i, pid in enumerate(ids)]}
        status = 202
    elif path.startswith("/generate/job1"):
        items = json.loads([c for c in calls if c[1] == "/generate"][-1][2])["pick_ids"]
        body = {"job_id": "job1", "batch_id": "b1", "status": "partial", "videos": [], "message": "1 reel(s) could not be rendered",
                "items": [{"index": i + 1, "pick_id": pid, "ref": pid, "status": "failed" if i == 1 else "done",
                           "reason": "Ayah audio could not be downloaded." if i == 1 else None,
                           "history_id": None if i == 1 else f"h{i}"} for i, pid in enumerate(items)]}
    elif path == "/history":
        body = {"videos": []}
    elif path == "/history/bulk-download-url":
        body = {"url": "/api/history/zip?x=1", "item_count": 6, "missing": []}
    elif re.fullmatch(r"/history/h\d+/url", path):
        body = {"url": "/api/video/x.mp4"}
    elif path == "/reciters":
        body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"},
                             {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad"}],
                "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
    elif path == "/music-list":
        body = {"music_files": []}
    elif path == "/video/x.mp4" or path.startswith("/history/zip"):
        route.fulfill(status=200, content_type="video/mp4", body=b"x", headers={"Content-Disposition": "attachment; filename=x.mp4"})
        return
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_page(browser, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
    context.add_init_script(STUB)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and not re.search(r"cdn\.|net::ERR|Failed to load resource", m.text) and page.errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.goto(f"{BASE}/generate.html", wait_until="networkidle")
    page.wait_for_function("window.userAccess")
    return page


def summary(page):
    return page.inner_text("#picks-summary")


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((360, 740), (390, 844), (768, 1024), (1280, 800)):
            calls.clear()
            page = open_page(browser, width, height)
            page.click("#btn-picks")
            page.wait_for_selector(".pk-row")
            tag = f"{width}px"
            fitting = [p for p in DEFAULT if p["bucket"] == 30 and p["mode"] == "urdu" and p["seconds"] <= 30]
            expect = min(25, len(fitting))
            check(f"{tag}: default view is 30 sec tab, Urdu, fitting picks preselected",
                  page.get_attribute(".pk-chips:nth-of-type(1) .pk-chip >> nth=0", "aria-pressed") == "true"
                  and summary(page).startswith(f"Selected {expect} "), summary(page))
            check(f"{tag}: all three tabs enabled", page.locator(".pk-chips").first.locator(".pk-chip:not([disabled])").count() == 3)
            check(f"{tag}: no horizontal scroll", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            small = page.evaluate("""[...document.querySelectorAll('#picks-root button, #picks-root .pk-label, #picks-root input[type=search]')]
                .filter(e => e.offsetParent).filter(e => e.getBoundingClientRect().height < 43.5).map(e => e.className.toString().slice(0, 30))""")
            check(f"{tag}: tap targets >= 44px", not small, str(small[:4]))

            page.click("text=Arabic-only")
            page.wait_for_selector(".pk-row")
            row = page.locator(".pk-row", has_text="2:255").first
            check(f"{tag}: over-cap pick is not selectable and says use the local app",
                  row.locator("input").is_disabled() and "local app" in row.inner_text(), row.inner_text())
            page.click("text=Clear")
            check(f"{tag}: Clear", summary(page).startswith("Selected 0 "))
            page.click("text=Select all fitting")
            check(f"{tag}: Select all fitting ignores over-cap picks", not page.locator(".pk-row.pk-off input:checked").count())
            page.click("text=Invert")
            page.click("text=Clear")
            page.fill(".pk-search", "112:1")
            check(f"{tag}: search filters", 0 < page.locator(".pk-row").count() < 10)
            page.fill(".pk-search", "")
            page.click("text=2 min")
            page.wait_for_selector(".pk-row")
            check(f"{tag}: 2 min tab starts empty", summary(page).startswith("Selected 0 "))
            page.click("text=30 sec")
            page.wait_for_selector(".pk-row")
            page.click("text=Urdu-only")
            page.wait_for_selector(".pk-row")
            page.click("text=Clear")
            page.locator(".pk-row input:not(:disabled)").nth(0).check()
            page.locator(".pk-row input:not(:disabled)").nth(1).check()
            page.locator(".pk-row input:not(:disabled)").nth(2).check()
            check(f"{tag}: summary counts ticks", summary(page).startswith("Selected 3 "), summary(page))
            page.reload(wait_until="networkidle")
            page.click("#btn-picks")
            page.wait_for_selector(".pk-row")
            check(f"{tag}: selection survives reload", summary(page).startswith("Selected 3 "), summary(page))

            page.click("#generate-button")
            page.wait_for_selector("#picks-batch:not([hidden]) .pk-brow", timeout=15000)
            page.wait_for_function("document.querySelector('#picks-batch .pk-st-failed')", timeout=15000)
            body = json.loads([c for c in calls if c[0] == "POST" and c[1] == "/generate"][-1][2])
            check(f"{tag}: generate sends pick ids only", len(body["pick_ids"]) == 3 and body["mode"] == "picks"
                  and body["content_mode"] == "urdu_only" and not body.get("verse"), str(body))
            check(f"{tag}: failed reel is reported with its reason", "could not be downloaded" in page.inner_text("#picks-batch"))
            check(f"{tag}: finished reels start ticked", page.locator("#picks-batch input:checked").count() == 2)
            check(f"{tag}: no horizontal scroll with batch", page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0)
            calls.clear()
            page.click("text=Download all from this batch")
            page.wait_for_timeout(1500)
            direct = [c for c in calls if re.fullmatch(r"/history/h\d+/url", c[1])]
            check(f"{tag}: 2 videos download directly (<=5)", len(direct) == 2 and not any(c[1] == "/history/bulk-download-url" for c in calls), str(calls))
            check(f"{tag}: no console errors", not page.errors, str(page.errors))
            if "--shots" in sys.argv:
                SHOTS.mkdir(exist_ok=True)
                page.screenshot(path=str(SHOTS / f"picks-{width}.png"), full_page=True)
            page.context.close()
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
