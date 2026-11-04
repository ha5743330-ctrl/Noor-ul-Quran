"""Playwright smoke: ask/soon/generate at 375 and 1280, no console errors, no horizontal scroll.
Supabase + API stubbed. Run: python tests/ui_smoke_check.py"""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8789
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
ACCESS = {"user_id": "p1", "email": "p@example.com", "is_admin": False, "premium_access": False, "daily_limit": 5, "daily_remaining": 5, "max_video_seconds": 35, "max_video_seconds_photo": 60}
BODIES = {"/access": ACCESS, "/reciters": {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"}, {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad"}], "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}, "/history": {"videos": []}, "/music-list": {"music_files": []}}
failures = []

server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in ("ask.html", "soon.html", "generate.html"):
            for w in (375, 1280):
                ctx = browser.new_context(viewport={"width": w, "height": 800})
                ctx.add_init_script(STUB)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
                # block third-party CDNs (offline-safe); stub config + API
                page.route("**/supabase-js@*/**", lambda r: r.abort())
                page.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
                page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
                page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(BODIES.get(r.request.url.split("/api", 1)[1].split("?")[0], {}))))
                page.goto(f"{BASE}/{name}", wait_until="load")
                page.wait_for_timeout(800)
                ok = not errors and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                print(("OK   " if ok else "FAIL ") + f"{name} {w}", errors[:3] if errors else "")
                if not ok:
                    failures.append(f"{name}@{w}")
                ctx.close()
        browser.close()
finally:
    server.terminate()
sys.exit(1 if failures else 0)
