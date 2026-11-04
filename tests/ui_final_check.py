"""Playwright (host): every page at 375/768/1280 has no horizontal scroll and no console errors.
API + Supabase stubbed, static server. Run: python tests/ui_final_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8787
BASE = f"http://localhost:{PORT}"
PAGES = ["index", "auth", "generate", "library", "profile", "admin-console", "search", "privacy", "terms", "reset-password", "signin"]
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: {access_token: 't', user: {id: 'a1', email: 'a@example.com', user_metadata: {}}}}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
HIT = {"surah": 2, "ayah": 255, "surah_name": "Al-Baqara", "ref": "2:255", "arabic": "ٱللَّهُ لَآ إِلَٰهَ إِلَّا هُوَ", "urdu": "اللہ", "english": "Allah! There is no deity save Him.",
       "tafsir_url": "https://quran.com/2/255/tafsirs", "generate_url": "/generate.html?pick=2:255-255"}


def api(route):
    path = re.sub(r"^.*?/api", "", urlparse(route.request.url).path)
    body = {}
    if path == "/access":
        body = {"user_id": "a1", "email": "a@example.com", "is_admin": True, "premium_access": True, "daily_limit": None, "daily_remaining": None, "max_video_seconds": 35}
    elif path == "/me":
        body = {"id": "a1", "email": "a@example.com", "display_name": "A", "avatar_url": None, "is_admin": True, "status": "active"}
    elif path == "/admin/users":
        body = {"users": [], "total": 0, "page": 1, "per_page": 50, "stats": {"total": 0, "premium": 0}}
    elif path == "/admin/audit":
        body = {"entries": [], "total": 0}
    elif path == "/admin/backgrounds":
        body = []
    elif path == "/admin/music":
        body = {"music_files": []}
    elif path == "/admin/analytics":
        body = {"days": [{"day": f"2026-10-{d:02d}", "views": 10 * d, "uniques": 5 * d, "signups": 1, "videos": 2, "failures": 0, "render_secs": 60, "renders": 2, "avg_render_secs": 30} for d in range(30, 0, -1)]}
    elif path == "/picks":
        body = {"cap": 35, "picks": [], "counts": {"total": 0}}
    elif path == "/history":
        body = {"videos": []}
    elif path == "/search":
        body = {"query": "x", "total": 1, "results": [HIT]}
    elif path == "/reciters":
        body = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "Alafasy"}, {"id": "shamshad_ali_khan", "language": "ur", "display_name": "Shamshad"}],
                "defaults": {"ar": "alafasy", "ur": "shamshad_ali_khan"}}
    elif path == "/music-list":
        body = {"music_files": []}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


failed = 0
server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in ((375, 800), (768, 1024), (1280, 800)):
            for name in PAGES:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                ctx.add_init_script(STUB)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: m.type == "error" and not re.search(r"cdn\.|net::ERR|Failed to load resource", m.text) and errors.append(m.text))
                page.route("**/supabase-js@*/**", lambda r: r.abort())
                page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
                page.route("**/api/**", api)
                url = f"{BASE}/{name}.html" + ("?q=baqara" if name == "search" else "")
                page.goto(url, wait_until="networkidle")
                over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                ok = over <= 0 and not errors
                failed += not ok
                print(f"{'OK  ' if ok else 'FAIL'} {width} {name} overflow={over} errors={errors}")
                ctx.close()
        browser.close()
finally:
    server.terminate()
sys.exit(1 if failed else 0)
