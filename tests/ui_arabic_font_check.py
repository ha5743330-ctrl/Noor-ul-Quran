"""Playwright (host): Quran font on search + generate at 375/1280: font loaded, no h-scroll, no console errors,
every glyph of 2:255 in the woff2 (no dotted circles). Run: python tests/ui_arabic_font_check.py"""
import json, subprocess, sys, time
from pathlib import Path
from fontTools.ttLib import TTFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT, BASE = 8789, "http://localhost:8789"
TXT = "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ"
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
STUB = "window.supabase={createClient:()=>({auth:{getSession:async()=>({data:{session:{access_token:'t',user:{id:'p1',email:'p@example.com',user_metadata:{}}}},error:null}),onAuthStateChange:()=>({data:{subscription:{}}}),signOut:async()=>({})}})};"
RES = {"total": 1, "results": [{"ref": "2:255", "surah_name": "Al-Baqarah", "arabic": TXT, "urdu": "اللہ", "english": "Allah", "generate_url": "/generate.html", "tafsir_url": "https://quran.com/2/255"}]}
fails = []
def check(n, ok):
    print(("OK   " if ok else "FAIL ") + n); ok or fails.append(n)

cmap = TTFont(ROOT / "public/fonts/AmiriQuran-Regular.woff2").getBestCmap()
check("all 2:255 codepoints in font", all(ord(c) in cmap for c in TXT))

srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w in (375, 1280):
            for name in ("search", "generate"):
                ctx = b.new_context(viewport={"width": w, "height": 800}); ctx.add_init_script(STUB)
                pg = ctx.new_page(); errs = []
                pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and "Daily limit fields" not in m.text and errs.append(m.text))
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.route("**/supabase-js@*/**", lambda r: r.abort())
                pg.route("**/*googleapis*/**", lambda r: r.abort()); pg.route("**/cdnjs*/**", lambda r: r.abort()); pg.route("**/cdn.tailwindcss.com/**", lambda r: r.abort()); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
                pg.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
                pg.route("**/api/**", lambda r: r.fulfill(content_type="application/json", body="{}"))
                pg.route("**/api/search*", lambda r: r.fulfill(content_type="application/json", body=json.dumps(RES)))
                pg.goto(f"{BASE}/{name}.html", wait_until="domcontentloaded")
                if name == "search":
                    pg.fill("#sr-q", "2:255"); pg.press("#sr-q", "Enter"); pg.wait_for_selector(".sr-ar")
                else:  # generate has no web Arabic text (previews are images): inject same class
                    pg.evaluate("t=>{const p=document.createElement('p');p.className='ar-text';p.lang='ar';p.dir='rtl';p.id='t';p.textContent=t;document.querySelector('main,body').prepend(p)}", TXT)
                sel = ".sr-ar" if name == "search" else "#t"
                pg.evaluate("document.fonts.ready"); pg.wait_for_timeout(300)
                check(f"{name}@{w} font loaded", pg.evaluate("document.fonts.check('24px \"Noor Quran\"', arguments=0)") if False else pg.evaluate("t=>document.fonts.load('24px \"Noor Quran\"',t).then(f=>f.length>0&&document.fonts.check('24px \"Noor Quran\"',t))", TXT))
                check(f"{name}@{w} no h-scroll", pg.evaluate("document.documentElement.scrollWidth<=innerWidth"))
                check(f"{name}@{w} no console errors", not errs or print(errs))
                pg.locator(sel).first.screenshot(path=str(ROOT / "tests/screenshots" / f"arabic-{name}-{w}.png"))
finally:
    srv.terminate()
sys.exit(1 if fails else 0)
