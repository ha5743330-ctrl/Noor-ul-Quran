"""WCAG AA contrast (>= 4.5:1) from computed colors: every visible text element, placeholders and disabled
controls, on every page, light and dark. Background = nearest painted ancestor (alpha-blended; for gradients
the worst stop counts). Text over photos/video is skipped. Run: python tests/ui_contrast_check.py"""
import sys
import time

from playwright.sync_api import sync_playwright

from ui_dark_check import PAGES, open_page, serve, ROOT

PORT = 8821
JS = r"""
() => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const over = (f, b) => [0, 1, 2].map(i => f[i] * f[3] + b[i] * (1 - f[3])).concat(1);
  const lum = (c) => { const [r, g, b] = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const pageBg = parse(getComputedStyle(document.documentElement).backgroundColor) || [255, 255, 255, 1];
  // returns list of candidate backgrounds (gradient -> each stop), or null when text sits on media
  // opacity of a node applies to it and everything inside, so a layer painted by n fades by n's opacity chain
  const oUp = (n) => { let o = 1; for (; n && n.nodeType === 1; n = n.parentElement) o *= +getComputedStyle(n).opacity; return o; };
  const fade = (c, o) => [c[0], c[1], c[2], c[3] * o];
  function backgrounds(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const o = oUp(n);
      if (n !== el && n.matches('video, img, canvas, .hero-background-slide')) return null;
      const stops = [...cs.backgroundImage.matchAll(/rgba?\([^)]+\)/g)].map(m => fade(parse(m[0]), o));
      if (/url\(/.test(cs.backgroundImage)) return null;
      const color = cs.backgroundColor.startsWith('rgb') ? fade(parse(cs.backgroundColor), o) : null;
      // top-first list: a node's gradient sits above its own background-color
      if (stops.length) layers.push(stops);
      if (color && color[3] > 0) layers.push([color]);
      if ((stops.length && stops.every(s => s[3] >= 0.99)) || (color && color[3] >= 0.99)) break;
    }
    // compose from the bottom (page) up; for gradients keep each stop as an alternative
    let cands = [pageBg];
    for (const layer of layers.reverse()) cands = cands.flatMap(b => layer.map(s => over(s, b)));
    return cands;
  }
  const out = [];
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.closest('[hidden], [aria-hidden="true"]'); };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '');
  function check(el, fg, kind) {
    const bgs = backgrounds(el); if (!bgs) return;
    const o = oUp(el);
    for (const bg of bgs) {
      const f = over([fg[0], fg[1], fg[2], fg[3] * o], bg);
      const r = ratio(f, bg);
      if (r < 4.5) { out.push({kind, el: name(el), text: (el.innerText || el.placeholder || '').trim().slice(0, 40), ratio: +r.toFixed(2),
        fg: getComputedStyle(el).color, bg: 'rgb(' + bg.slice(0, 3).map(Math.round).join(',') + ')'}); return; }
    }
  }
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el.closest('svg, video, .lib-thumb, #image-result-box, .ad-photo')) continue;
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const cs = getComputedStyle(el);
    if (hasText) check(el, parse(cs.color), el.matches(':disabled') ? 'disabled' : el.matches('a') ? 'link' : el.matches('button, .btn') ? 'button' : 'text');
    if (el.matches('input[placeholder], textarea[placeholder]') && !el.value)
      check(el, parse(getComputedStyle(el, '::placeholder').color), 'placeholder');
    if (el.matches('select, input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]):not([type=color])') && (el.value || el.tagName === 'SELECT'))
      check(el, parse(cs.color), 'field');
  }
  return out;
}
"""
# one sample of every chip/badge/message/state class, so pairs that need real data are still measured
SAMPLES = """
() => { const m = document.querySelector('main') || document.body; const box = document.createElement('div'); box.className = 'ui-card';
  box.style.padding = '12px'; box.innerHTML = `
  <p class="pf-badge">badge</p> <p class="pf-badge warn">warn</p> <p class="pf-badge bad">bad</p>
  <span class="pk-badge">pk</span> <span class="pk-badge pk-bad">pk bad</span> <span class="pk-badge pk-warn">pk warn</span>
  <span class="pk-badge pk-st-failed">failed</span> <span class="pk-badge pk-st-queued">queued</span>
  <p class="pk-summary">summary</p> <p class="pk-reason">reason</p> <p class="pk-msg">msg</p> <p class="pf-msg ok">ok</p> <p class="pf-msg err">err</p>
  <div class="ad-msg ok" style="position:static">ok msg</div> <div class="ad-msg err" style="position:static">err msg</div>
  <div class="ui-state">empty</div> <div class="ui-state" data-kind="error">error</div> <div class="account-banner">banner</div>
  <p class="nav-quota">quota</p> <div class="lib-tabs"><button aria-pressed="true">on</button><button>off</button></div>
  <div class="ad-chips"><button aria-pressed="true">on</button><button>off</button></div>
  <button class="pk-chip" aria-pressed="true">chip</button> <button class="pk-chip">chip</button>
  <button class="btn btn-primary">p</button> <button class="btn btn-outline">o</button> <button class="btn btn-danger">d</button>
  <button class="btn btn-danger-outline">do</button> <a class="nav-link" aria-current="page" href="#">cur</a> <a href="#">plain link</a>
  <p class="text-amber-400">amber</p> <p class="text-emerald-400">emerald</p> <p class="text-green-400">green</p> <p class="text-red-400">red</p>
  <p class="text-slate-400">slate</p> <p class="text-rose-400">rose</p> <p class="hint">hint</p> <dl class="ad-diag"><div><dt>dt</dt><dd>dd</dd></div></dl>
  <div class="danger-zone ui-card"><p>danger zone text</p></div>`; m.prepend(box); }
"""
DISABLE_ALL = "() => document.querySelectorAll('button, .btn').forEach(b => { b.disabled = true; b.style.transition = 'none'; })"



def main():
    server = serve(ROOT / "public", PORT)
    time.sleep(1)
    failures = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for theme in ("light", "dark"):
                for name in PAGES:
                    for width in (375, 1280):
                        ctx, page, _ = open_page(browser, f"http://localhost:{PORT}", name, width, theme, storage=theme)
                        if name == "generate":  # include the collapsed style/music cards
                            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                        if name == "admin-console":
                            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                        page.evaluate(SAMPLES)
                        found = page.evaluate(JS)
                        page.evaluate(DISABLE_ALL)
                        page.wait_for_timeout(50)
                        found += [f for f in page.evaluate(JS) if f["kind"] == "disabled"]
                        for f in found:
                            key = (theme, f["fg"], f["bg"], f["kind"])
                            failures.setdefault(key, {**f, "pages": set()})["pages"].add(f"{name}@{width}")
                        ctx.close()
            browser.close()
    finally:
        server.terminate()
    for (theme, fg, bg, kind), f in sorted(failures.items(), key=lambda kv: kv[1]["ratio"]):
        print(f"FAIL {theme:5} {f['ratio']:>5} {kind:11} fg={fg} bg={bg} {f['el'][:60]} '{f['text']}' {sorted(f['pages'])[:3]}")
    print(f"{len(failures)} failing color pairs")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
