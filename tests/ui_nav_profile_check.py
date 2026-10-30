"""Playwright (host): shared nav + burger, no horizontal scroll, profile validation, DELETE gate, library ZIP threshold.
API and Supabase are stubbed. Run: python tests/ui_nav_profile_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8781
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'u', email: 'a@b.co', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
  updateUser: async () => ({error: null}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
ME = {"id": "u", "email": "a@b.co", "email_confirmed": True, "new_email": "", "display_name": "Aisha Khan",
      "avatar_url": None, "status": "active", "is_admin": True, "premium_access": True, "created_at": "2026-01-01T00:00:00Z"}
ACCESS = {"user_id": "u", "email": "a@b.co", "is_admin": False, "premium_access": False, "daily_limit": 5,
          "daily_remaining": 3, "max_video_seconds": 35}
HISTORY = {"videos": [
    {"id": f"v{i}", "type": "video", "filename": f"v{i}.mp4", "surah": 1, "start_ayah": 1, "end_ayah": 2,
     "created_at": f"2026-10-0{i}T10:00:00+00:00", "has_file": True, "download_count": 0}
    for i in range(1, 7)
] + [
    {"id": f"i{i}", "type": "image", "filename": f"i{i}.png", "reference": "1:1", "created_at": f"2026-10-1{i}T10:00:00+00:00",
     "has_file": True, "thumb_url": f"/api/video/i{i}.png"}
    for i in range(1, 3)
]}
failures = []
calls = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def api(route):
    request = route.request
    path = re.sub(r"^.*?/api", "", request.url.split("?")[0])
    calls.append((request.method, path))
    body = {}
    if path == "/me":
        body = ME
    elif path == "/access":
        body = {**ACCESS, "is_admin": True, "daily_limit": None, "daily_remaining": None} if "admin-console" in request.headers.get("referer", "") else ACCESS
    elif path == "/me/usage":
        body = {"videos": 6, "images": 2, "bytes": 5 * 1024 * 1024}
    elif path == "/history":
        body = HISTORY
    elif re.fullmatch(r"/history/\w+/url", path):
        body = {"url": f"/api/video/{path.split('/')[2]}.mp4?dl=1", "expires_at": 0}
    elif path == "/history/bulk-download-url":
        body = {"url": "/api/history/zip?x=1", "item_count": 6}
    elif path == "/history/bulk-delete":
        body = {"deleted": 1}
    elif path.startswith("/admin/users"):
        body = {"users": [], "total": 0, "page": 1, "per_page": 50, "stats": {"total": 0, "premium": 0}}
    elif path == "/admin/audit":
        body = {"entries": []}
    elif path == "/admin/diagnostics":
        body = {}
    elif path in ("/admin/backgrounds",):
        body = []
    elif path == "/admin/music":
        body = {"music_files": []}
    elif path.startswith("/video/") or path.startswith("/history/zip"):
        route.fulfill(status=200, body=b"x", headers={"Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename=x.bin"})
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def new_page(browser, width, height=800, access=None):
    context = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    context.add_init_script(STUB)  # the CDN script (with SRI) is aborted, so the stub is the only supabase
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", api)
    page.errors = errors
    return page


def settled(locator, state):
    """Drawer animates visibility for 200 ms, so wait for the end state instead of sampling."""
    try:
        locator.wait_for(state=state, timeout=2000)
        return True
    except Exception:
        return False


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- nav: no horizontal scroll at 360/768/1280 on every page ---
        for width in (360, 768, 1280):
            for name in ("index", "library", "profile", "admin-console", "auth", "privacy", "terms", "generate"):
                page = new_page(browser, width)
                page.goto(f"{BASE}/{name}.html", wait_until="networkidle")
                page.wait_for_selector(".nav-bar")
                check(f"{width}px {name}: no horizontal scroll", overflow(page) <= 0, str(overflow(page)))
                check(f"{width}px {name}: no page errors", not page.errors, str(page.errors))
                burger_visible = page.locator(".nav-burger").is_visible()
                check(f"{width}px {name}: burger {'shown' if width <= 820 else 'hidden'}", burger_visible == (width <= 820))
                page.context.close()

        # --- desktop nav: items, active link, chip ---
        page = new_page(browser, 1280)
        page.goto(f"{BASE}/library.html", wait_until="networkidle")
        page.wait_for_selector(".nav-chip-name")
        labels = page.locator(".nav-links .nav-link").all_inner_texts()
        check("desktop nav items", [x.strip() for x in labels] == ["Home", "Generate", "Library", "Profile", "Admin", "Sign Out"], str(labels))
        check("active item is Library", page.locator('.nav-links [aria-current="page"]').inner_text().strip() == "Library")
        check("chip shows display name", page.locator(".nav-chip-name").inner_text() == "Aisha Khan")
        page.context.close()

        # --- burger: open, Esc, backdrop, focus trap, scroll lock, link closes ---
        page = new_page(browser, 360)
        page.goto(f"{BASE}/library.html", wait_until="networkidle")
        page.wait_for_function("document.querySelector('#nav-quota')?.textContent.includes('3 / 5')")  # nav fully built
        burger = page.locator(".nav-burger")
        drawer = page.locator("#nav-drawer")
        check("burger target >= 44px", burger.bounding_box()["height"] >= 44 and burger.bounding_box()["width"] >= 44)
        check("drawer closed initially", settled(drawer, "hidden") and burger.get_attribute("aria-expanded") == "false")
        burger.click()
        check("burger opens drawer", settled(drawer, "visible") and burger.get_attribute("aria-expanded") == "true")
        check("aria-controls points at drawer", burger.get_attribute("aria-controls") == "nav-drawer")
        check("body scroll locked", page.evaluate("getComputedStyle(document.body).overflow") == "hidden")
        check("videos-left counter", "3 / 5" in page.locator("#nav-quota").inner_text())
        check("drawer has Privacy/Terms + Sign Out", all(drawer.get_by_text(t).count() for t in ("Privacy", "Terms", "Sign Out")))
        tall = all(box["height"] >= 44 for box in (loc.bounding_box() for loc in drawer.locator("a, button").all()) if box)
        check("drawer tap targets >= 44px", tall)
        for _ in range(12):
            page.keyboard.press("Tab")
        check("focus stays in drawer", page.evaluate("document.getElementById('nav-drawer').contains(document.activeElement)"))
        page.keyboard.press("Escape")
        check("Esc closes", settled(drawer, "hidden") and burger.get_attribute("aria-expanded") == "false")
        check("focus returns to burger", page.evaluate("document.activeElement.classList.contains('nav-burger')"))
        check("scroll unlocked", page.evaluate("getComputedStyle(document.body).overflow") != "hidden")
        burger.click()
        settled(drawer, "visible")
        page.mouse.click(350, 400)
        check("backdrop click closes", settled(drawer, "hidden"))
        burger.click()
        settled(drawer, "visible")
        drawer.get_by_role("link", name="Profile").click()
        page.wait_for_url("**/profile.html")
        page.wait_for_selector(".nav-chip")
        check("route change leaves drawer closed", settled(page.locator("#nav-drawer"), "hidden"))
        page.context.close()

        # --- profile validation + DELETE gate ---
        page = new_page(browser, 360)
        page.goto(f"{BASE}/profile.html", wait_until="networkidle")
        page.wait_for_function("document.getElementById('pf-name').value === 'Aisha Khan'")
        check("name filled via textContent/value", page.locator("#pf-email").inner_text() == "a@b.co")
        page.fill("#pf-name", "A")
        page.click("#pf-name-form button[type=submit]")
        check("short name rejected", "2 se 40" in page.locator("#pf-name-msg").inner_text())
        page.fill("#pf-new-email", "not-an-email")
        page.click("#pf-email-form button[type=submit]")
        check("bad email rejected", "email" in page.locator("#pf-email-msg").inner_text().lower())
        page.fill("#pf-pass-current", "oldpassword")
        page.fill("#pf-pass-new", "short")
        page.fill("#pf-pass-confirm", "short")
        page.click("#pf-pass-form button[type=submit]")
        check("short password rejected", "8" in page.locator("#pf-pass-msg").inner_text())
        page.fill("#pf-pass-new", "longenough1")
        page.fill("#pf-pass-confirm", "different12")
        page.click("#pf-pass-form button[type=submit]")
        check("password mismatch rejected", "match" in page.locator("#pf-pass-msg").inner_text())
        check("no password call sent", ("POST", "/me/password") not in calls)
        toggle = page.locator("#pf-pass-form .pw-toggle").first
        toggle.click()
        check("show/hide password", page.locator("#pf-pass-current").get_attribute("type") == "text")
        page.set_input_files("#pf-avatar-file", files=[{"name": "a.gif", "mimeType": "image/gif", "buffer": b"GIF89a"}])
        check("avatar wrong type rejected", "PNG" in page.locator("#pf-avatar-msg").inner_text())
        page.set_input_files("#pf-avatar-file", files=[{"name": "a.png", "mimeType": "image/png", "buffer": b"\x00" * (2 * 1024 * 1024 + 1)}])
        check("avatar over 2 MB rejected", "2 MB" in page.locator("#pf-avatar-msg").inner_text())
        check("no avatar upload sent", ("POST", "/me/avatar") not in calls)
        page.locator("#pf-danger summary").click()
        delete = page.locator("#pf-delete")
        check("delete disabled initially", delete.is_disabled())
        page.fill("#pf-del-pass", "pw")
        page.fill("#pf-del-confirm", "delete")
        check("delete needs exact DELETE", delete.is_disabled())
        page.fill("#pf-del-confirm", "DELETE")
        check("delete enabled after DELETE + password", delete.is_enabled())
        delete.click()
        check("confirm dialog shown before deleting", page.locator("dialog.confirm").is_visible() and ("POST", "/me/delete") not in calls)
        page.locator("dialog.confirm").get_by_text("Cancel").click()
        check("cancel sends nothing", ("POST", "/me/delete") not in calls)
        page.context.close()

        # --- library: filter, select-all, direct <= 5, ZIP > 5, bulk delete confirm ---
        page = new_page(browser, 360)
        page.goto(f"{BASE}/library.html", wait_until="networkidle")
        page.wait_for_selector(".lib-item")
        check("library lists all items", page.locator(".lib-item").count() == 8)
        titles = page.locator(".lib-item .lib-meta").all_inner_texts()
        check("newest first", "Image" in titles[0], titles[0])
        page.click('[data-filter="image"]')
        check("filter images", page.locator(".lib-item").count() == 2)
        page.click('[data-filter="video"]')
        check("filter videos", page.locator(".lib-item").count() == 6)
        page.click("#lib-select-toggle")
        page.locator(".lib-item input[type=checkbox]").nth(0).check()
        page.locator(".lib-item input[type=checkbox]").nth(1).check()
        page.locator(".lib-item input[type=checkbox]").nth(2).check()
        page.locator(".lib-item input[type=checkbox]").nth(3).check()
        page.locator(".lib-item input[type=checkbox]").nth(4).check()
        calls.clear()
        page.click("#lib-download")
        page.wait_for_timeout(2600)
        urls = [c for c in calls if re.fullmatch(r"/history/\w+/url", c[1])]
        check("5 items download directly", len(urls) == 5 and ("POST", "/history/bulk-download-url") not in calls, str(calls))
        page.click("#lib-select-toggle")
        page.click("#lib-select-all")
        check("select all selects 6", page.locator("#lib-count").inner_text() == "6 selected")
        calls.clear()
        page.click("#lib-download")
        page.wait_for_timeout(800)
        check("6 items use one ZIP", ("POST", "/history/bulk-download-url") in calls and not any(re.fullmatch(r"/history/\w+/url", c[1]) for c in calls), str(calls))
        page.click("#lib-select-toggle")
        page.click("#lib-select-all")
        page.click("#lib-delete")
        check("bulk delete asks for confirmation", page.locator("dialog.confirm").is_visible() and ("POST", "/history/bulk-delete") not in calls)
        page.locator("dialog.confirm").get_by_text("Delete", exact=True).last.click()
        page.wait_for_timeout(500)
        check("bulk delete sent after confirm", ("POST", "/history/bulk-delete") in calls)
        page.context.close()

        # --- logged-out nav ---
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        context.add_init_script(STUB.replace("session: {access_token: 't', user: {id: 'u', email: 'a@b.co', user_metadata: {}}}", "session: null"))
        page.route("**/supabase-js@*/**", lambda r: r.abort())
        page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
        page.goto(f"{BASE}/privacy.html", wait_until="networkidle")
        page.wait_for_selector(".nav-bar")
        labels = [x.strip() for x in page.locator(".nav-links .nav-link").all_inner_texts()]
        check("logged-out nav is Home | Sign In", labels == ["Home", "Sign In"], str(labels))
        context.close()

        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
