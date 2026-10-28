"""Playwright overflow + console-error sweep at 375 and 1440. Run: python tests/ui_check.py [base_url]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
PAGES = ["index", "auth", "signin", "generate", "admin-console", "admin-panel"]
OUT = Path(__file__).parent / "output" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
failed = False
with sync_playwright() as p:
    browser = p.chromium.launch()
    for width, height in ((375, 800), (1440, 900)):
        for name in PAGES:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE}/{name}.html", wait_until="networkidle")
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            page.screenshot(path=str(OUT / f"check-{name}-{width}.png"), full_page=True)
            errors = [e for e in errors if "cdn.tailwindcss.com" not in e]
            ok = overflow <= 0 and not errors
            failed |= not ok
            print(f"{'OK  ' if ok else 'FAIL'} {width} {name} -> {page.url.split(BASE)[-1]} overflow={overflow} errors={errors}")
            page.close()
    browser.close()
sys.exit(1 if failed else 0)
