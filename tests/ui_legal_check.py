"""Playwright checks for legal pages, signup consent and password reset (Supabase stubbed, static server).
Run: python tests/ui_legal_check.py"""
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8778
BASE = f"http://localhost:{PORT}"
STUB = """
window.__calls = {reset: [], update: []};
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: null}, error: null}),
  onAuthStateChange: (cb) => { window.__authCb = cb; return {data: {subscription: {}}}; },
  resetPasswordForEmail: async (email, opts) => { window.__calls.reset.push([email, opts.redirectTo]); return {error: {message: 'User not found'}}; },
  updateUser: async (a) => { window.__calls.update.push(a); return {error: null}; },
  signOut: async () => ({}),
}})};
"""
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_CONTACT_EMAIL='me@test.dev';"
failures = []


def check(name, condition, detail=""):
    print(("OK   " if condition else "FAIL ") + name + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def new_page(browser, path):
    page = browser.new_page()
    page.route("**/supabase.js", lambda r: r.fulfill(body=STUB, content_type="application/javascript"))
    page.route("**/config.js", lambda r: r.fulfill(body=CONFIG, content_type="application/javascript"))

    def serve_html(route):
        html = (ROOT / "public" / route.request.url.split("/")[-1].split("?")[0].split("#")[0]).read_text(encoding="utf-8")
        route.fulfill(body=re.sub(r'integrity="[^"]+"', "", html), content_type="text/html")

    page.route(re.compile(r".*/(auth|reset-password)\.html.*"), serve_html)
    page.goto(f"{BASE}/{path}", wait_until="domcontentloaded")
    return page


server = subprocess.Popen(
    [sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Footer links resolve on every page that has them.
        for name in ("index", "generate", "auth", "admin-console", "privacy", "terms", "reset-password"):
            page = browser.new_page()
            footer = re.search(r"<footer.*?</footer>", page.request.get(f"{BASE}/{name}.html").text(), re.S).group(0)
            hrefs = re.findall(r'href="(/(?:privacy|terms)\.html)"', footer)
            check(f"{name}: footer links Privacy and Terms", sorted(hrefs) == ["/privacy.html", "/terms.html"], str(hrefs))
            for href in hrefs:
                status = page.request.get(f"{BASE}{href}").status
                check(f"{name}: {href} -> 200", status == 200, str(status))
            page.close()

        page = new_page(browser, "privacy.html")
        page.wait_for_timeout(300)
        check("privacy: contact email from config.js", page.locator(".contact-email").first.inner_text() == "me@test.dev")
        check("privacy: states 3-day deletion and daily limit", "3 days" in page.content() and "5 videos" in page.content())
        page.close()

        # Signup consent checkbox is required and links open in a new tab.
        page = new_page(browser, "auth.html")
        page.click("#tab-signup")
        check("signup: terms checkbox is required", page.eval_on_selector("#terms", "e => e.required"))
        check("signup: terms links open in new tab", page.locator("label[for=terms] a[target=_blank]").count() == 2)
        for field, value in (("signup-name", "A B"), ("signup-email", "a@b.co"), ("signup-password", "secret123")):
            page.fill(f"#{field}", value)
        check("signup: form invalid until agreed", page.eval_on_selector("#form-signup", "f => !f.checkValidity()"))
        page.close()

        # Forgot password: neutral message (even when Supabase reports an error) + 60 s cooldown.
        page = new_page(browser, "auth.html")
        page.click("#link-forgot")
        page.fill("#forgot-email", "nobody@example.com")
        page.click("#btn-forgot")
        page.wait_for_timeout(300)
        check("forgot: neutral message shown", "If this email exists" in page.inner_text("#auth-alert"))
        check("forgot: redirectTo is /reset-password.html", page.evaluate("window.__calls.reset[0][1]") == f"{BASE}/reset-password.html")
        check("forgot: 60 s cooldown on button", page.is_disabled("#btn-forgot") and "Wait 60s" in page.inner_text("#btn-forgot"))
        page.close()

        # Reset page: error without a token, and on an expired link.
        page = new_page(browser, "reset-password.html")
        page.wait_for_timeout(3400)
        check("reset: no token -> error + request-again link", "invalid or has expired" in page.inner_text("#reset-alert") and page.is_visible("#request-again") and not page.is_visible("#reset-form"))
        page.close()
        page = new_page(browser, "reset-password.html#error=access_denied&error_code=otp_expired")
        page.wait_for_timeout(300)
        check("reset: expired link -> error", "invalid or has expired" in page.inner_text("#reset-alert") and not page.is_visible("#reset-form"))
        page.close()

        # Reset page with a recovery session: validation blocks bad input; valid input updates.
        page = new_page(browser, "reset-password.html#access_token=x&type=recovery")
        page.evaluate("window.__authCb('PASSWORD_RECOVERY', {})")
        check("reset: form shown on PASSWORD_RECOVERY", page.is_visible("#reset-form"))
        page.fill("#new-password", "short")
        page.fill("#confirm-password", "short")
        page.click("#reset-submit")
        check("reset: <8 chars blocked", "8 characters" in page.inner_text("#reset-alert") and page.evaluate("window.__calls.update.length") == 0)
        page.fill("#new-password", "longenough1")
        page.fill("#confirm-password", "different12")
        page.click("#reset-submit")
        check("reset: mismatch blocked", "do not match" in page.inner_text("#reset-alert") and page.evaluate("window.__calls.update.length") == 0)
        page.check("#show-password")
        check("reset: show/hide toggle", page.get_attribute("#new-password", "type") == "text")
        page.fill("#confirm-password", "longenough1")
        page.click("#reset-submit")
        page.wait_for_timeout(300)
        check("reset: valid input calls updateUser", page.evaluate("window.__calls.update.length") == 1 and "Password updated" in page.inner_text("#reset-alert"))
        page.close()
        browser.close()
finally:
    server.terminate()

sys.exit(1 if failures else 0)
