// Shared UI states: empty, skeleton, error+retry, gate, toast, field errors, offline / slow-server / session banners.
// Loaded before nav.js on every page. Wraps window.fetch for API calls only (slow notice, GET retries, 401). All text via textContent.
(function () {
    "use strict";
    const API = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
    const apiHref = new URL(API, window.location.href).href;
    const nativeFetch = window.fetch.bind(window);
    const SLOW_MS = 4000;
    const RETRIES = 3;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function icon(name) {
        const i = el("i", `fa-solid ${name}`);
        i.setAttribute("aria-hidden", "true");
        return i;
    }
    function button(label, className, onClick) {
        const b = el(onClick ? "button" : "a", `btn ${className || "btn-primary"} btn-sm`, label);
        if (onClick) { b.type = "button"; b.addEventListener("click", onClick); }
        return b;
    }
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    // ---- friendly text: never raw JSON, stack traces or file paths ----
    function friendly(error) {
        const status = error?.status;
        if (!navigator.onLine) return "Internet nahi hai. Connection wapas aane par dobara try karein.";
        if (status === 401) return "Session khatam, dobara login karein.";
        if (status === 403) return "Is kaam ki ijazat nahi hai.";
        if (status === 404) return "Yeh cheez nahi mili.";
        if (status === 429) return "Bohat zyada requests. Thori dair baad try karein.";
        if (status >= 500 || error instanceof TypeError) return "Server se rabta nahi hua. Dobara try karein.";
        const text = String(error?.message || "");
        return text && text.length < 160 && !/[{}\\]|\/[\w.-]+\/|Traceback|HTTP \d/.test(text) ? text : "Kuch masla aa gaya. Dobara try karein.";
    }

    // <ul>/<ol> hosts get an <li> wrapper so the markup stays valid
    const wrapTag = (host) => (/^(UL|OL)$/.test(host.tagName) ? "li" : "div");

    // ---- building blocks ----
    function empty(host, { icon: name = "fa-inbox", title, text, action }) {
        const box = el(wrapTag(host), "st-empty");
        box.append(icon(name), el("p", "st-title", title));
        if (text) box.append(el("p", "st-text", text));
        if (action) {
            const a = action.href ? button(action.label, "btn-primary") : button(action.label, "btn-primary", action.onClick);
            if (action.href) a.href = action.href;
            box.append(a);
        }
        host.replaceChildren(box);
        host.hidden = false;
        return box;
    }

    function error(host, { what, err, onRetry }) {
        const box = el(wrapTag(host), "st-empty st-error");
        box.setAttribute("role", "alert");
        box.append(icon("fa-triangle-exclamation"), el("p", "st-title", `${what} load nahi ho saka`), el("p", "st-text", friendly(err)));
        if (onRetry) {
            const retry = button("Retry", "btn-primary", async () => {
                retry.disabled = true;
                try { await onRetry(); } finally { retry.disabled = false; }
            });
            box.append(retry);
        }
        host.replaceChildren(box);
        host.hidden = false;
        return box;
    }

    function skeleton(host, count = 3, kind = "row") {
        const wrap = el(wrapTag(host), `st-skel-wrap st-skel-${kind}`);
        wrap.setAttribute("aria-busy", "true");
        wrap.setAttribute("aria-label", "Loading");
        for (let i = 0; i < count; i += 1) wrap.append(el("div", "st-skel"));
        host.replaceChildren(wrap);
        host.hidden = false;
        return wrap;
    }

    // Full-width reason + button, replaces a page's main content (denied / signed out / blocked).
    function gate(host, { icon: name = "fa-lock", title, text, actions = [] }) {
        const box = el("section", "ui-card st-gate");
        box.setAttribute("role", "alert");
        box.append(icon(name), el("h2", "st-title", title), el("p", "st-text", text));
        const row = el("div", "st-actions");
        actions.forEach((a) => {
            const b = a.href ? button(a.label, a.secondary ? "btn-outline" : "btn-primary") : button(a.label, a.secondary ? "btn-outline" : "btn-primary", a.onClick);
            if (a.href) b.href = a.href;
            row.append(b);
        });
        box.append(row);
        // hide (not remove) the page content so scripts that still reference it keep working
        [...host.children].forEach((child) => { child.style.display = "none"; });
        host.append(box);
        return box;
    }

    const loginHref = () => `/auth.html?next=${encodeURIComponent(location.pathname + location.search)}`;
    const gates = {
        signedOut: (host, what = "Is page") => gate(host, {
            icon: "fa-right-to-bracket", title: "Pehle login karein", text: `${what} istemal karne ke liye account chahiye.`,
            actions: [{ label: "Login", href: loginHref() }, { label: "Home", href: "/", secondary: true }]
        }),
        notAdmin: (host) => gate(host, {
            icon: "fa-shield-halved", title: "Sirf admin ke liye", text: "Aap ka account is page ko kholne ki ijazat nahi rakhta.",
            actions: [{ label: "Home", href: "/" }]
        })
    };

    // ---- toast ----
    function toast(message, kind = "ok") {
        let tray = document.getElementById("st-toasts");
        if (!tray) {
            tray = el("div", "st-toasts");
            tray.id = "st-toasts";
            tray.setAttribute("role", "status");
            tray.setAttribute("aria-live", "polite");
            document.body.append(tray);
        }
        const node = el("div", `st-toast st-toast-${kind}`);
        node.append(icon(kind === "err" ? "fa-circle-exclamation" : "fa-circle-check"), el("span", "", message));
        tray.append(node);
        setTimeout(() => node.remove(), 4000);
    }

    // ---- inline field errors: message under the exact field, value untouched ----
    function fieldError(input, message) {
        if (!input) return;
        const id = `${input.id || "field"}-err`;
        let note = document.getElementById(id);
        if (!note) {
            note = el("p", "st-field-err");
            note.id = id;
            note.setAttribute("aria-live", "polite");
            // icon-wrapped inputs and checkboxes: put the message after the wrapper so layout stays intact
            (input.closest(".relative") || (input.type === "checkbox" && input.closest("label")) || input).insertAdjacentElement("afterend", note);
            input.addEventListener("input", () => clearField(input));
        }
        note.textContent = message;
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", id);
        input.classList.add("st-invalid");
    }
    function clearField(input) {
        const note = document.getElementById(`${input.id}-err`);
        if (note) note.textContent = "";
        input.removeAttribute("aria-invalid");
        input.classList.remove("st-invalid");
    }

    // ---- banners (offline / slow server / session expired) ----
    function banner(id, kind) {
        let node = document.getElementById(id);
        if (!node) {
            node = el("div", `st-banner st-banner-${kind}`);
            node.id = id;
            node.setAttribute("role", kind === "session" ? "alert" : "status");
            document.body.prepend(node);
        }
        return node;
    }
    function setBanner(id, kind, on, fill) {
        if (!on) { document.getElementById(id)?.remove(); return; }
        const node = banner(id, kind);
        if (!node.childElementCount) fill(node);
    }

    function syncOffline() {
        setBanner("st-offline", "offline", !navigator.onLine, (node) => {
            node.append(icon("fa-wifi"), el("span", "", "Internet nahi hai"));
        });
        if (navigator.onLine) toastBack();
    }
    let wasOffline = !navigator.onLine;
    function toastBack() {
        if (wasOffline) { wasOffline = false; toast("Internet wapas aa gaya"); }
    }
    window.addEventListener("offline", () => { wasOffline = true; syncOffline(); });
    window.addEventListener("online", syncOffline);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncOffline);
    else syncOffline();

    function sessionExpired() {
        if (/^\/(auth|reset-password)/.test(location.pathname)) return;
        setBanner("st-session", "session", true, (node) => {
            node.append(icon("fa-clock"), el("span", "", "Session khatam, dobara login karein"));
            const login = button("Login", "btn-primary", async () => {
                login.disabled = true;
                try { await window.supabaseClient?.auth.signOut(); } catch { /* local sign-out is enough */ }
                location.href = loginHref(); // auth.html returns here via ?next=
            });
            node.append(login);
        });
    }

    let slowCount = 0;
    function slowNotice(delta) {
        slowCount += delta;
        setBanner("st-slow", "slow", slowCount > 0, (node) => {
            node.append(icon("fa-mug-hot"), el("span", "", "Server jaag raha hai, 30-60 s lagte hain"));
            const bar = el("span", "st-progress");
            bar.setAttribute("aria-hidden", "true");
            node.append(bar);
        });
    }

    function onlineAgain() {
        if (navigator.onLine) return Promise.resolve();
        return new Promise((resolve) => window.addEventListener("online", resolve, { once: true }));
    }

    // ---- fetch wrapper: API calls only ----
    window.fetch = async function noorFetch(input, init) {
        const url = new URL(String(input?.url || input), location.href).href;
        if (!url.startsWith(apiHref) || /\/track(\?|$)/.test(url)) return nativeFetch(input, init);
        const method = String(init?.method || input?.method || "GET").toUpperCase();
        const idempotent = method === "GET";
        let slow = false;
        const timer = setTimeout(() => { slow = true; slowNotice(1); }, SLOW_MS);
        try {
            for (let attempt = 0; ; attempt += 1) {
                try {
                    if (idempotent) await onlineAgain(); // queued: waits, nothing is lost
                    const response = await nativeFetch(input, init);
                    if (idempotent && [502, 503, 504].includes(response.status) && attempt < RETRIES) { await sleep(800 * (attempt + 1)); continue; }
                    const auth = new Headers(init?.headers || input?.headers || {}).has("Authorization");
                    if (response.status === 401 && auth) sessionExpired();
                    return response;
                } catch (err) {
                    if (!idempotent || err?.name === "AbortError" || attempt >= RETRIES) throw err;
                    await sleep(800 * (attempt + 1));
                }
            }
        } finally {
            clearTimeout(timer);
            if (slow) slowNotice(-1);
        }
    };

    window.noorUi = { empty, error, skeleton, gate, gates, toast, fieldError, clearField, friendly, loginHref, sessionExpired };
})();
