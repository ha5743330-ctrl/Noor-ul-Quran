"""Playwright (host): shared UI states (empty, skeleton, error+retry, offline, slow/retry, gates, 401, validation, toasts)
in light+dark at 375/1280. API + Supabase stubbed. No console errors, no horizontal scroll. Run: python tests/ui_states_check.py"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8791
BASE = f"http://localhost:{PORT}"
CONFIG = "window.NOOR_SUPABASE_CONFIG={url:'https://x.supabase.co',anonKey:'k',apiBaseUrl:'/api'};window.NOOR_API_BASE_URL='/api';"
STUB = """
window.__calls = {reset: 0, signOut: 0};
window.supabase = {createClient: () => ({auth: {
  getSession: async () => ({data: {session: %s}, error: null}),
  onAuthStateChange: (cb) => { %s; return {data: {subscription: {}}}; },
  signOut: async () => { window.__calls.signOut++; return {}; },
  signInWithPassword: async () => ({data: {}, error: {message: 'Invalid login credentials'}}),
  resetPasswordForEmail: async () => { window.__calls.reset++; return {error: null}; },
}})};
"""
SESSION = "{access_token: 't', user: {id: 'p1', email: 'p@example.com', user_metadata: {}}}"
ACCESS = {"user_id": "p1", "email": "p@example.com", "is_admin": False, "premium_access": True, "daily_limit": 30,
          "daily_remaining": 20, "max_video_seconds": 35, "max_video_seconds_photo": 35}
AYAH = {"ref": "2:153", "surah_name": "Al-Baqarah", "arabic": "x", "urdu": "y", "english": "z", "generate_url": "/generate.html?pick=2:153-153"}
VIDEO = {"id": "v1", "type": "video", "surah": 1, "start_ayah": 1, "end_ayah": 2, "created_at": "2026-01-01T00:00:00Z", "has_file": True}
failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


class Env:
    """One browser context + page with a programmable API stub. routes[path] = callable(route) | (status, body) | 'hold'."""

    def __init__(self, browser, theme, width, name, routes=None, signed_in=True, recovery=False, query=""):
        self.ctx = browser.new_context(viewport={"width": width, "height": 800})
        self.ctx.add_init_script(STUB % (SESSION if signed_in else "null", "setTimeout(() => cb('PASSWORD_RECOVERY'), 30)" if recovery else ""))
        self.ctx.add_init_script(f"try{{localStorage.setItem('noor-theme','{theme}')}}catch(e){{}}")
        self.page = self.ctx.new_page()
        self.errors, self.held, self.hits = [], [], []
        self.routes = routes or {}
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: m.type == "error" and not re.search(r"Failed to load resource|net::ERR|ERR_", m.text) and self.errors.append(m.text))
        self.page.route("**/supabase-js@*/**", lambda r: r.abort())
        self.page.route("**/cdn.tailwindcss.com/**", lambda r: r.abort())
        self.page.route("**/config.js", lambda r: r.fulfill(content_type="application/javascript", body=CONFIG))
        self.page.route("**/api/**", self.api)
        self.page.goto(f"{BASE}/{name}{query}", wait_until="domcontentloaded")

    def api(self, route):
        path = re.sub(r"^.*?/api", "", route.request.url.split("?")[0])
        self.hits.append(path)
        rule = self.routes.get(path)
        if rule == "hold":
            self.held.append(route)
            return
        if callable(rule):
            return rule(route)
        if rule is not None:
            status, body = rule
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        defaults = {"/access": ACCESS, "/history": {"videos": []}, "/music-list": {"music_files": []}, "/me": {"display_name": "P", "email": "p@example.com", "is_admin": False}}
        if path == "/reciters":
            defaults[path] = {"reciters": [{"id": "alafasy", "language": "ar", "display_name": "A"}, {"id": "u", "language": "ur", "display_name": "U"}], "defaults": {"ar": "alafasy", "ur": "u"}}
        if path == "/estimate":
            defaults[path] = {"estimated_seconds": 4.0, "fits_up_to": 99, "tolerance_fits_up_to": 99, "within_tolerance": True, "total_count": 1, "range_seconds": 4.0}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(defaults.get(path, {})))

    def release(self, status=200, body=None):
        self.held.pop(0).fulfill(status=status, content_type="application/json", body=json.dumps(body or {}))

    def clean(self, label):
        h = self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        check(f"{label}: no h-scroll", h)
        check(f"{label}: no console errors", not self.errors, str(self.errors[:2]))

    def close(self):
        self.ctx.close()


def raw_leak(text):
    return bool(re.search(r"[{}]|/api/|Traceback|\\\\|HTTP \d", text))


def run(browser, theme, width, heavy):
    tag = f"[{theme} {width}]"

    # 1+2 Library: skeleton while loading, then empty state with one next-step button
    e = Env(browser, theme, width, "library.html", {"/history": "hold"})
    e.page.wait_for_selector("#lib-skel .st-skel")
    check(f"{tag} library skeleton", e.page.locator("#lib-skel .st-skel").count() >= 3)
    e.release(body={"videos": []})
    e.page.wait_for_selector("#lib-empty .st-empty")
    check(f"{tag} library empty: icon + reason + button", e.page.locator("#lib-empty i").count() == 1 and e.page.inner_text("#lib-empty .st-title") != "" and e.page.locator("#lib-empty a.btn[href='/generate.html']").count() == 1)
    check(f"{tag} library skeleton removed", e.page.locator(".st-skel").count() == 0)
    e.clean(f"{tag} library empty")
    e.close()

    # 3 Library error: friendly text + Retry recovers
    state = {"n": 0}

    def flaky(route):
        state["n"] += 1
        if state["n"] == 1:
            return route.fulfill(status=500, content_type="application/json", body='{"detail":"Traceback /srv/app/main.py line 3 {boom}"}')
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"videos": [VIDEO]}))
    e = Env(browser, theme, width, "library.html", {"/history": flaky})
    e.page.wait_for_selector("#lib-skel .st-error")
    txt = e.page.inner_text("#lib-skel")
    check(f"{tag} library error names what failed + no raw text", "Library" in txt and not raw_leak(txt), txt)
    e.page.click("#lib-skel .st-error button")
    e.page.wait_for_selector(".lib-item")
    check(f"{tag} retry recovers", e.page.locator(".lib-item").count() == 1 and e.page.locator(".st-error").count() == 0)
    e.clean(f"{tag} library retry")
    e.close()

    # 4 Offline banner + auto-hide
    e = Env(browser, theme, width, "library.html", {"/history": (200, {"videos": [VIDEO]})})
    e.page.wait_for_selector(".lib-item")
    e.ctx.set_offline(True)
    e.page.wait_for_selector("#st-offline")
    check(f"{tag} offline banner", "Internet nahi hai" in e.page.inner_text("#st-offline"))
    e.clean(f"{tag} offline")
    e.ctx.set_offline(False)
    e.page.wait_for_selector("#st-offline", state="detached")
    check(f"{tag} offline banner auto-hides", e.page.locator("#st-offline").count() == 0)
    e.close()

    # 6 Search: first view empty + no results with 5 topics + skeleton + error
    e = Env(browser, theme, width, "search.html", {"/search": "hold"})
    e.page.wait_for_selector("#sr-extra .st-empty")
    check(f"{tag} search first view empty state", e.page.locator("#sr-extra .st-empty a, #sr-extra .st-empty button").count() == 1)
    e.page.fill("#sr-q", "x")
    e.page.click("#sr-form button[type=submit]")
    check(f"{tag} search short query -> inline field error", "2 harf" in e.page.inner_text("#sr-q-err") and e.page.get_attribute("#sr-q", "aria-invalid") == "true" and e.page.input_value("#sr-q") == "x")
    e.page.fill("#sr-q", "zzzz")
    e.page.click("#sr-form button[type=submit]")
    e.page.wait_for_selector("#sr-extra .st-skel")
    e.release(body={"results": [], "total": 0})
    e.page.wait_for_selector("#sr-extra .st-topics")
    check(f"{tag} search no results: friendly + 5 topics", e.page.locator("#sr-extra .st-topics button").count() == 5)
    e.clean(f"{tag} search none")
    e.close()

    e = Env(browser, theme, width, "search.html", {"/search": (500, {"detail": "x"})}, query="?q=patience")
    e.page.wait_for_selector("#sr-extra .st-error")
    check(f"{tag} search error has Retry", e.page.locator("#sr-extra .st-error button").count() == 1 and not raw_leak(e.page.inner_text("#sr-extra")))
    e.close()

    # 6/10 Ask AI: skeleton, no results + topics, daily limit state, session
    asks = {"status": "none", "answer": "", "cited": [], "ayat": [], "message": "No match."}
    e = Env(browser, theme, width, "ask.html", {"/ask": "hold"})
    e.page.click(".ask-chips button")
    e.page.wait_for_selector("#ask-extra .st-skel")
    check(f"{tag} ask skeleton", True)
    e.release(body=asks)
    e.page.wait_for_selector("#ask-extra .st-topics")
    check(f"{tag} ask no results: 5 topics", e.page.locator("#ask-extra .st-topics button").count() == 5)
    e.close()
    e = Env(browser, theme, width, "ask.html", {"/ask": (200, {"status": "limit", "answer": "", "cited": [], "ayat": [AYAH], "message": "Daily limit reached, try tomorrow."})})
    e.page.click(".ask-chips button")
    e.page.wait_for_selector("#ask-extra .st-empty")
    check(f"{tag} ask daily-limit state + next step", "limit" in e.page.inner_text("#ask-extra").lower() and e.page.locator("#ask-extra a[href='/search.html']").count() == 1)
    e.clean(f"{tag} ask limit")
    e.close()

    # 7 Permission denied
    e = Env(browser, theme, width, "admin-console.html", {})
    e.page.wait_for_selector(".st-gate")
    check(f"{tag} non-admin gate with Home", e.page.locator(".st-gate a[href='/']").count() == 1 and "admin" in e.page.inner_text(".st-gate").lower())
    e.clean(f"{tag} admin non-admin")
    e.close()
    e = Env(browser, theme, width, "admin-console.html", {}, signed_in=False)
    e.page.wait_for_selector(".st-gate")
    check(f"{tag} signed-out admin gate has Login", "/auth.html?next=" in (e.page.get_attribute(".st-gate a.btn-primary", "href") or ""))
    e.close()
    e = Env(browser, theme, width, "generate.html", {}, signed_in=False)
    e.page.wait_for_selector(".st-gate")
    check(f"{tag} signed-out Generate gate (not blank)", e.page.locator(".st-gate a").count() == 2 and "login" in e.page.inner_text(".st-gate").lower())
    e.clean(f"{tag} generate signed out")
    e.close()
    e = Env(browser, theme, width, "library.html", {"/history": (403, {"error": "account_blocked", "reason": "spam"})})
    e.page.wait_for_selector("#account-wall")
    check(f"{tag} blocked user wall with reason + button", "spam" in e.page.inner_text("#account-wall") and e.page.locator("#account-wall button").count() == 1)
    e.close()

    # 8 Session expired on 401 -> banner -> login goes back to same page
    e = Env(browser, theme, width, "library.html", {"/history": (401, {"detail": "expired"})})
    e.page.wait_for_selector("#st-session")
    check(f"{tag} 401 banner text", "Session khatam, dobara login karein" in e.page.inner_text("#st-session"))
    e.page.click("#st-session button")
    e.page.wait_for_url(re.compile(r"auth\.html\?next=%2Flibrary\.html"))
    check(f"{tag} login returns to same page via next", True)
    e.close()

    # 9 Validation: login / signup / forgot / reset / generate range / admin picks add
    e = Env(browser, theme, width, "auth.html", {}, signed_in=False)
    e.page.wait_for_selector("#login-email")
    e.page.fill("#login-email", "not-an-email")
    e.page.click("#btn-login")
    check(f"{tag} login email error under field + value kept", "email" in e.page.inner_text("#login-email-err").lower() and e.page.input_value("#login-email") == "not-an-email" and e.page.get_attribute("#login-email-err", "aria-live") == "polite")
    check(f"{tag} login password error", "Password" in e.page.inner_text("#login-password-err"))
    e.page.evaluate("showForgot()")
    e.page.fill("#forgot-email", "a@b.co")
    e.page.click("#btn-forgot")
    e.page.wait_for_selector(".st-toast")
    check(f"{tag} reset email sent toast", "Reset email" in e.page.inner_text(".st-toast") and e.page.evaluate("window.__calls.reset") == 1)
    e.clean(f"{tag} auth")
    e.close()
    e = Env(browser, theme, width, "reset-password.html", {}, recovery=True)
    e.page.wait_for_selector("#reset-form:not(.hidden)")
    e.page.fill("#new-password", "short")
    e.page.click("#reset-submit")
    check(f"{tag} reset short password inline", "8" in e.page.inner_text("#new-password-err") and e.page.input_value("#new-password") == "short")
    e.page.fill("#new-password", "longenough1")
    e.page.fill("#confirm-password", "different1")
    e.page.click("#reset-submit")
    check(f"{tag} reset mismatch inline", "match" in e.page.inner_text("#confirm-password-err"))
    e.close()
    e = Env(browser, theme, width, "generate.html", {})
    e.page.wait_for_function("window.userAccess")
    e.page.fill("#input-pick", "abc")
    e.page.click("#generate-button")
    check(f"{tag} generate range inline error + value kept", "Surah:Start-End" in e.page.inner_text("#input-pick-err") and e.page.input_value("#input-pick") == "abc")
    # Picks: skeleton -> empty with next step
    e.close()

    # 10 Daily limit reached (video)
    e = Env(browser, theme, width, "generate.html", {"/access": (200, {**ACCESS, "daily_remaining": 0})})
    e.page.wait_for_selector("#limit-state")
    check(f"{tag} video daily-limit state", e.page.is_disabled("#generate-button") and "limit" in e.page.inner_text("#limit-state").lower())
    e.clean(f"{tag} generate limit")
    e.close()

    # Picks: empty + error with Retry
    e = Env(browser, theme, width, "generate.html", {"/picks": (200, {"picks": [], "cap": 35, "counts": {"total": 0}})})
    e.page.wait_for_function("window.userAccess")
    e.page.evaluate("window.NoorPicks.show()")
    e.page.wait_for_selector(".pk-list .st-empty")
    check(f"{tag} picks empty state with button", e.page.locator(".pk-list .st-empty button").count() == 1)
    e.close()
    e = Env(browser, theme, width, "generate.html", {"/picks": (500, {"detail": "boom"})})
    e.page.wait_for_function("window.userAccess")
    e.page.evaluate("window.NoorPicks.show()")
    e.page.wait_for_selector(".pk-list .st-error")
    check(f"{tag} picks error + Retry", e.page.locator(".pk-list .st-error button").count() == 1 and not raw_leak(e.page.inner_text(".pk-list")))
    e.close()

    # Admin lists: skeleton, empty, error + Retry, picks-add validation
    adm = {"/access": (200, {**ACCESS, "is_admin": True}), "/admin/users": (200, {"users": [], "total": 0, "stats": {"total": 0, "premium": 0}}),
           "/admin/audit": (200, {"entries": [], "total": 0}), "/admin/photos": (200, []), "/admin/backgrounds": (200, []),
           "/admin/music": (200, {"music_files": []}), "/picks": (200, {"picks": [], "cap": 35, "counts": {"total": 0}})}
    e = Env(browser, theme, width, "admin-console.html", dict(adm, **{"/admin/music": (500, {"detail": "x"})}))
    e.page.wait_for_selector("#users-list .st-empty")
    e.page.wait_for_selector("#music-list .st-error", state="attached")
    check(f"{tag} admin users empty state", e.page.locator("#users-list .st-empty .st-title").count() == 1)
    check(f"{tag} admin list error + Retry", e.page.locator("#music-list .st-error button").count() == 1 and not raw_leak(e.page.inner_text("#music-list")))
    e.page.evaluate("document.getElementById('picks-section')?.querySelector('details')?.setAttribute('open','')")
    e.page.fill("#ap-ref", "garbage")
    e.page.dispatch_event("#ap-add", "submit")
    check(f"{tag} admin picks add inline error", "Surah:Start-End" in e.page.inner_text("#ap-ref-err") and e.page.input_value("#ap-ref") == "garbage")
    e.close()

    if not heavy:
        return

    # 5 Slow / cold start: banner after 4 s, progress hint, hides after response
    e = Env(browser, theme, width, "search.html", {"/search": "hold"}, query="?q=patience")
    e.page.wait_for_selector("#st-slow", timeout=8000)
    check(f"{tag} slow banner text + progress", "Server jaag raha hai, 30-60 s lagte hain" in e.page.inner_text("#st-slow") and e.page.locator("#st-slow .st-progress").count() == 1)
    e.clean(f"{tag} slow")
    e.release(body={"results": [], "total": 0})
    e.page.wait_for_selector("#st-slow", state="detached")
    check(f"{tag} slow banner hides", True)
    e.close()

    # 5 Auto-retry up to 3 times (cold start 503s) then success, no error shown
    n = {"c": 0}

    def cold(route):
        n["c"] += 1
        if n["c"] <= 3:
            return route.fulfill(status=503, content_type="application/json", body="{}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"videos": [VIDEO]}))
    e = Env(browser, theme, width, "library.html", {"/history": cold})
    e.page.wait_for_selector(".lib-item", timeout=15000)
    check(f"{tag} auto-retried 3x then succeeded", n["c"] == 4 and e.page.locator(".st-error").count() == 0, str(n))
    e.close()

    # network failure: 3 retries then error with Retry
    m = {"c": 0}

    def drop(route):
        m["c"] += 1
        route.abort()
    e = Env(browser, theme, width, "library.html", {"/history": drop})
    e.page.wait_for_selector("#lib-skel .st-error", timeout=15000)
    check(f"{tag} gave up after 1+3 tries then Retry shown", m["c"] == 4 and e.page.locator("#lib-skel .st-error button").count() == 1, str(m))
    e.close()

    # 10 video-ready toast
    e = Env(browser, theme, width, "generate.html", {"/generate": (200, {"status": "completed", "filename": "a.mp4", "videos": [{"filename": "a.mp4", "id": "v1", "type": "video", "caption": "c"}], "message": "ok"})})
    e.page.wait_for_function("window.userAccess")
    e.page.fill("#input-pick", "112:1-4")
    e.page.click("#generate-button")
    e.page.wait_for_selector(".st-toast")
    check(f"{tag} video ready toast", "Video tayyar" in e.page.inner_text(".st-toast"))
    e.clean(f"{tag} generate done")
    e.close()


server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "-d", str(ROOT / "public")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        first = True
        for theme in ("light", "dark"):
            for width in (375, 1280):
                run(browser, theme, width, heavy=first or (theme, width) == ("dark", 1280))
                first = False
        browser.close()
finally:
    server.terminate()

print(f"\n{len(failures)} failed" if failures else "\nall ui_states checks passed")
sys.exit(1 if failures else 0)
