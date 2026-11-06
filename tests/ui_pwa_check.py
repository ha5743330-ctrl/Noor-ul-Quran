"""PWA check: manifest valid, every page wired, SW registers, /api + POST never cached, offline fallback, install button.
Run: python tests/ui_pwa_check.py"""
import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / "public"
PORT = 8790
BASE = f"http://localhost:{PORT}"
STUB = """
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: null}, error: null}),
  onAuthStateChange: () => ({data: {subscription: {}}}), signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
failures = []


def check(name, ok):
    print(("OK   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def png_size(path):
    d = Path(path).read_bytes()
    return struct.unpack(">II", d[16:24])


# --- static: manifest + pages ---
m = json.loads((PUB / "manifest.webmanifest").read_text(encoding="utf-8"))
css = (PUB / "ui.css").read_text(encoding="utf-8")
light = css.split("[data-theme")[0] if "[data-theme" in css else css
tok = lambda n: re.search(rf"--{n}:\s*(#[0-9a-fA-F]{{6}})", css).group(1).lower()
check("manifest: name/display/start_url", m["name"] == "Noor Ul Quran" and m["display"] == "standalone" and m["start_url"] == "/")
check("manifest: colors come from ui.css tokens", m["theme_color"].lower() == tok("accent") and m["background_color"].lower() == tok("page"))
icons = {(i["sizes"], i["purpose"]): i for i in m["icons"]}
check("manifest: icons 192/512 any + 512 maskable", {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= set(icons))
check("manifest: icon files exist with declared size", all(
    (PUB / i["src"].lstrip("/")).exists() and "x".join(map(str, png_size(PUB / i["src"].lstrip("/")))) == i["sizes"] for i in m["icons"]))
check("apple-touch-icon is 180x180", png_size(PUB / "assets/apple-touch-icon.png") == (180, 180))
unwired = [p.name for p in PUB.glob("*.html") if p.name != "offline.html" and not all(
    s in p.read_text(encoding="utf-8") for s in ('rel="manifest"', 'rel="apple-touch-icon"', "apple-mobile-web-app-capable", "/pwa.js"))]
check(f"all pages wired (missing: {unwired})", not unwired)
sw = (PUB / "sw.js").read_text(encoding="utf-8")
check("sw.js skips /api and non-GET", '"/api/"' in sw and 'request.method !== "GET"' in sw)

# --- browser ---
server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(PUB)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_init_script(STUB)
        ctx.route("**/supabase-js@*/**", lambda r: r.abort())
        ctx.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
        ctx.route("**/cdnjs.cloudflare.com/**", lambda r: r.abort())
        ctx.route("**/config.js", lambda r: r.fulfill(body=CONFIG, content_type="application/javascript"))
        ctx.route("**/api/**", lambda r: r.fulfill(status=200, json={"ok": True}))
        page = ctx.new_page()
        page.goto(f"{BASE}/index.html", wait_until="load")
        page.evaluate("navigator.serviceWorker.ready")
        check("SW registers and activates", page.evaluate("navigator.serviceWorker.ready.then(r => !!r.active)"))
        page.reload(wait_until="load")  # now controlled by the SW
        check("page is SW-controlled", page.evaluate("!!navigator.serviceWorker.controller"))
        page.evaluate("""fetch('/api/secret').then(r => r.text())""")
        page.evaluate("""fetch('/api/secret', {method: 'POST', body: '{}'}).then(r => r.text())""")
        page.evaluate("""fetch('/ui.css').then(r => r.text())""")
        cached = page.evaluate("""caches.keys().then(async ks => { const out = [];
            for (const k of ks) for (const r of await (await caches.open(k)).keys()) out.push(new URL(r.url).pathname); return out; })""")
        check("shell cached", "/ui.css" in cached and "/offline.html" in cached)
        check(f"/api never cached (cache: {cached})", not any(u.startswith("/api") or u == "/config.js" for u in cached))
        # install button: hidden until the browser offers install
        check("install button hidden before prompt", page.evaluate("[...document.querySelectorAll('.pwa-install')].every(b => b.hidden)"))
        page.evaluate("""const e = new Event('beforeinstallprompt'); e.prompt = () => { window.__prompted = true; };
            window.dispatchEvent(e);""")
        btn = page.locator(".pwa-install").first
        check("install button shown on beforeinstallprompt", btn.is_visible())
        btn.click()
        check("install click calls prompt() and hides button", page.evaluate("window.__prompted === true") and not btn.is_visible())
        # offline fallback
        ctx.set_offline(True)
        page.goto(f"{BASE}/ask.html", wait_until="load")
        check("offline navigation shows fallback page", "You are offline" in page.inner_text("body"))
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
