"""Playwright dark-mode check: every page at 375/1280 in dark (no console errors, no sideways scroll,
theme set before first paint), toggle flips + persists, system preference is the default.
Optional: python tests/ui_dark_check.py <other public dir>  -> also pixel-compares LIGHT mode against it.
Supabase + API stubbed. Screenshots go to tests/screenshots/dark/."""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "tests" / "screenshots" / "dark"
PAGES = ("index", "generate", "library", "profile", "admin-console", "ask", "soon", "privacy", "terms", "auth", "search")
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  getUser: async () => ({data: {user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {unsubscribe() {}}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
ACCESS = {"user_id": "p1", "email": "p@example.com", "is_admin": True, "premium_access": True, "daily_limit": 5,
          "daily_remaining": 5, "max_video_seconds": 35, "max_video_seconds_photo": 60, "display_name": "Pat"}
BODIES = {
    "/access": ACCESS, "/me": ACCESS, "/history": {"videos": []}, "/music-list": {"music_files": []},
    "/reciters": {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"}], "defaults": {"ar": "alafasy", "ur": "alafasy"}},
    "/admin/users": {"users": [], "total": 0}, "/admin/audit": {"entries": [], "total": 0},
    "/admin/analytics": {"days": [{"day": "2026-11-03", "views": 9, "uniques": 4, "signups": 1, "videos": 2, "failures": 0}], "status": {"enabled": True}},
}


def serve(public, port):
    return subprocess.Popen([sys.executable, "-m", "http.server", str(port), "-d", str(public)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_page(browser, base, name, width, scheme, storage=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
    ctx.add_init_script(STUB + (f"try{{localStorage.setItem('noor-theme','{storage}')}}catch(e){{}}" if storage else ""))
    # record the theme at the very first script run in <body> (i.e. before first paint)
    ctx.add_init_script("document.addEventListener('readystatechange',()=>{if(!window.__t0)window.__t0=document.documentElement.dataset.theme},{once:true})")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
    page.route("**/supabase-js@*/**", lambda r: r.abort())
    page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
    page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                               body=json.dumps(BODIES.get(r.request.url.split("/api", 1)[1].split("?")[0], {}))))
    page.goto(f"{base}/{name}.html", wait_until="load")
    page.wait_for_timeout(900)
    return ctx, page, errors


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    other = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    servers = [serve(ROOT / "public", 8801)] + ([serve(other, 8802)] if other else [])
    time.sleep(1)
    failures = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for name in PAGES:
                for width in (375, 1280):
                    ctx, page, errors = open_page(browser, "http://localhost:8801", name, width, "dark")
                    theme = page.evaluate("document.documentElement.dataset.theme")
                    first = page.evaluate("window.__t0 || document.documentElement.dataset.theme")
                    wide = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
                    page.screenshot(path=str(SHOTS / f"{name}-{width}.png"), full_page=True)
                    ok = not errors and theme == "dark" and first == "dark" and not wide
                    print(("OK   " if ok else "FAIL ") + f"dark {name} {width} bg={bg}", errors[:2])
                    if not ok:
                        failures.append(f"dark {name}@{width}")
                    ctx.close()

            # toggle flips, persists across reload, and a saved choice beats the system preference
            ctx, page, errors = open_page(browser, "http://localhost:8801", "ask", 1280, "light")
            assert page.evaluate("document.documentElement.dataset.theme") == "light"
            page.click(".nav-bar .theme-toggle")
            assert page.evaluate("document.documentElement.dataset.theme") == "dark"
            assert page.evaluate("localStorage.getItem('noor-theme')") == "dark"
            assert page.get_attribute(".nav-bar .theme-toggle", "aria-label") == "Switch to light mode"
            assert page.evaluate("document.querySelector('meta[name=theme-color]').content") == "#07130f"
            page.reload(wait_until="load")
            assert page.evaluate("document.documentElement.dataset.theme") == "dark"
            ctx.close()
            ctx, page, errors = open_page(browser, "http://localhost:8801", "generate", 375, "dark", storage="light")
            assert page.evaluate("document.documentElement.dataset.theme") == "light"
            page.click(".nav-burger")
            page.wait_for_timeout(300)
            page.click(".nav-drawer .theme-toggle")
            assert page.evaluate("document.documentElement.dataset.theme") == "dark"
            assert page.inner_text(".nav-drawer .theme-toggle").strip() == "Light mode"
            ctx.close()
            print("OK   toggle + persistence + drawer toggle")

            if other:  # light mode must look exactly like before
                from PIL import Image, ImageChops
                for name in PAGES:
                    for width in (375, 1280):
                        shots = []
                        for port in (8801, 8802):
                            ctx, page, _ = open_page(browser, f"http://localhost:{port}", name, width, "light")
                            page.evaluate("document.querySelectorAll('.theme-toggle').forEach(b => b.remove())")
                            page.evaluate("Promise.all([...document.images].map(i => (i.loading = 'eager', i.complete || new Promise(r => (i.onload = i.onerror = r)))))")
                            page.wait_for_timeout(300)
                            page.add_style_tag(content="*{caret-color:transparent!important}")
                            path = SHOTS / f"light-{port}-{name}-{width}.png"
                            page.screenshot(path=str(path), full_page=True)
                            shots.append(Image.open(path).convert("RGB"))
                            ctx.close()
                        a, b = shots
                        same_size = a.size == b.size
                        diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 16 else 0).getbbox() if same_size else "size"
                        print(("OK   " if not diff else "DIFF ") + f"light {name} {width}", diff or "")
                        if diff:
                            failures.append(f"light {name}@{width}")
            browser.close()
    finally:
        for s in servers:
            s.terminate()
    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
