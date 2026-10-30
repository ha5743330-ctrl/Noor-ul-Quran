// Shared header + mobile sidebar. Every page has <header id="site-header"></header>; this fills it.
// All user-provided text goes through textContent.
(function () {
    const API = (window.NOOR_API_BASE_URL || "/api").replace(/\/$/, "");
    const apiOrigin = new URL(API, window.location.origin).origin;

    // --- blocked / deactivated handling for every API call on every page ---
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
        const response = await nativeFetch(...args);
        if (response.status === 403) {
            const url = String(args[0]?.url || args[0]);
            if (url.startsWith(API) || url.startsWith(apiOrigin + "/api")) {
                response.clone().json().then((body) => {
                    if (body?.error === "account_blocked") showWall(body.reason);
                    else if (body?.error === "account_deactivated") showBanner();
                }).catch(() => {});
            }
        }
        return response;
    };

    let ctl = null;
    let listening = false;

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

    async function signOutNow() {
        if (typeof handleSignOut === "function") return handleSignOut();
        await window.supabaseClient?.auth.signOut();
        window.location.href = "/auth.html";
    }

    function showWall(reason) {
        if (document.getElementById("account-wall")) return;
        const wall = el("div", "account-wall");
        wall.id = "account-wall";
        wall.setAttribute("role", "alertdialog");
        wall.setAttribute("aria-modal", "true");
        wall.setAttribute("aria-labelledby", "wall-title");
        const card = el("div", "account-wall-card");
        const title = el("h2", "", "Account blocked / Account band hai");
        title.id = "wall-title";
        card.append(title, el("p", "", "An admin has blocked this account, so you cannot use the studio."));
        if (reason) card.append(el("p", "account-wall-reason", `Reason: ${reason}`));
        const out = el("button", "btn btn-primary", "Sign out");
        out.type = "button";
        out.addEventListener("click", signOutNow);
        card.append(out);
        wall.append(card);
        document.body.append(wall);
        out.focus();
    }

    function showBanner() {
        if (document.getElementById("account-banner")) return;
        const banner = el("div", "account-banner");
        banner.id = "account-banner";
        banner.setAttribute("role", "alert");
        banner.append(el("span", "", "Your account is deactivated. Reactivate to use the studio again."));
        const button = el("button", "btn btn-primary btn-sm", "Reactivate");
        button.type = "button";
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                const { data: { session } } = await window.supabaseClient.auth.getSession();
                const response = await nativeFetch(`${API}/me/reactivate`, {
                    method: "POST", headers: { Authorization: `Bearer ${session.access_token}` }
                });
                if (!response.ok) throw new Error();
                window.location.reload();
            } catch {
                button.disabled = false;
                button.textContent = "Retry";
            }
        });
        banner.append(button);
        document.body.prepend(banner);
    }

    function avatar(profile, size) {
        const wrap = el("span", `nav-avatar nav-avatar-${size}`);
        if (profile.avatar_url) {
            const img = el("img");
            img.alt = "";
            img.src = profile.avatar_url;
            wrap.append(img);
        } else {
            wrap.textContent = (profile.display_name || "?").trim().charAt(0).toUpperCase();
        }
        return wrap;
    }

    function link(href, label, iconName, current) {
        const a = el("a", "nav-link");
        a.href = href;
        a.append(icon(iconName), el("span", "", label));
        if (current) a.setAttribute("aria-current", "page");
        return a;
    }

    function build(host, state) {
        const path = window.location.pathname.replace(/\/index\.html$/, "/");
        const is = (p) => path === p || path === p.replace(/\.html$/, "");
        const items = state.signedIn
            ? [
                ["/", "Home", "fa-house", path === "/"],
                ["/generate.html", "Generate", "fa-wand-magic-sparkles", is("/generate.html")],
                ["/library.html", "Library", "fa-photo-film", is("/library.html")],
                ["/profile.html", "Profile", "fa-user", is("/profile.html")],
                ...(state.profile?.is_admin ? [["/admin-console.html", "Admin", "fa-shield-halved", is("/admin-console.html")]] : [])
            ]
            : [
                ["/", "Home", "fa-house", path === "/"],
                [`/auth.html?next=${encodeURIComponent(path + window.location.search)}`, "Sign In", "fa-right-to-bracket", is("/auth.html")]
            ];

        const bar = el("div", "nav-bar");
        const burger = el("button", "nav-burger");
        burger.type = "button";
        burger.setAttribute("aria-label", "Open menu");
        burger.setAttribute("aria-expanded", "false");
        burger.setAttribute("aria-controls", "nav-drawer");
        burger.append(icon("fa-bars"));

        const brand = el("a", "nav-brand");
        brand.href = "/";
        brand.setAttribute("aria-label", "Noor ul Quran home");
        const logo = el("img");
        logo.src = "/assets/logo.png";
        logo.alt = "";
        brand.append(logo, el("span", "", "Noor ul Quran"));

        const nav = el("nav", "nav-links");
        nav.setAttribute("aria-label", "Main navigation");
        items.forEach((item) => nav.append(link(...item)));
        if (state.signedIn) {
            const out = el("button", "nav-link nav-signout");
            out.type = "button";
            out.append(icon("fa-right-from-bracket"), el("span", "", "Sign Out"));
            out.addEventListener("click", signOutNow);
            nav.append(out);
        }

        bar.append(burger, brand, nav);
        if (state.signedIn && state.profile) {
            const chip = el("a", "nav-chip");
            chip.href = "/profile.html";
            chip.title = state.profile.email || "";
            chip.append(avatar(state.profile, "sm"), el("span", "nav-chip-name", state.profile.display_name));
            bar.append(chip);
        }
        host.replaceChildren(bar);

        // --- drawer ---
        const backdrop = el("div", "nav-backdrop");
        const drawer = el("aside", "nav-drawer");
        drawer.id = "nav-drawer";
        drawer.setAttribute("role", "dialog");
        drawer.setAttribute("aria-modal", "true");
        drawer.setAttribute("aria-label", "Menu");
        const head = el("div", "nav-drawer-head");
        if (state.signedIn && state.profile) {
            const who = el("div", "nav-drawer-user");
            who.append(avatar(state.profile, "md"));
            const text = el("div", "nav-drawer-who");
            text.append(el("strong", "", state.profile.display_name), el("small", "", state.profile.email));
            who.append(text);
            head.append(who);
        } else {
            head.append(el("strong", "", "Noor ul Quran"));
        }
        const close = el("button", "nav-close");
        close.type = "button";
        close.setAttribute("aria-label", "Close menu");
        close.append(icon("fa-xmark"));
        head.append(close);

        const list = el("nav", "nav-drawer-links");
        list.setAttribute("aria-label", "Menu");
        items.forEach((item) => list.append(link(...item)));
        drawer.append(head, list);

        if (state.signedIn) {
            const quota = el("p", "nav-quota");
            quota.id = "nav-quota";
            quota.textContent = "Aaj ki videos baqi: …";
            drawer.append(quota);
        }
        const foot = el("div", "nav-drawer-foot");
        const legal = el("div", "nav-legal");
        [["/privacy.html", "Privacy"], ["/terms.html", "Terms"]].forEach(([href, label]) => {
            const a = el("a", "", label);
            a.href = href;
            legal.append(a);
        });
        foot.append(legal);
        if (state.signedIn) {
            const out = el("button", "btn btn-outline nav-drawer-signout");
            out.type = "button";
            out.append(icon("fa-right-from-bracket"), el("span", "", "Sign Out"));
            out.addEventListener("click", signOutNow);
            foot.append(out);
        }
        drawer.append(foot);
        document.body.append(backdrop, drawer);

        // --- open / close behaviour ---
        let open = false;
        const focusables = () => [...drawer.querySelectorAll("a[href], button:not([disabled])")];
        function setOpen(next) {
            if (open === next) return;
            open = next;
            drawer.classList.toggle("is-open", open);
            backdrop.classList.toggle("is-open", open);
            burger.setAttribute("aria-expanded", String(open));
            document.documentElement.classList.toggle("nav-locked", open);
            if (open) {
                close.focus();
            } else {
                burger.focus();
            }
        }
        burger.addEventListener("click", () => setOpen(true));
        close.addEventListener("click", () => setOpen(false));
        backdrop.addEventListener("click", () => setOpen(false));
        drawer.addEventListener("click", (event) => {
            if (event.target.closest("a[href]")) setOpen(false); // route change
        });
        ctl = { isOpen: () => open, setOpen, focusables };
        if (listening) return;
        listening = true;
        document.addEventListener("keydown", (event) => {
            if (!ctl.isOpen()) return;
            if (event.key === "Escape") {
                ctl.setOpen(false);
            } else if (event.key === "Tab") {
                const nodes = ctl.focusables();
                const first = nodes[0];
                const last = nodes[nodes.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
        });
        const closeAll = () => ctl.setOpen(false);
        window.addEventListener("pageshow", closeAll);
        window.addEventListener("hashchange", closeAll);
        window.addEventListener("popstate", closeAll);
        window.matchMedia("(min-width: 821px)").addEventListener("change", (e) => e.matches && closeAll());
    }

    async function api(path, token) {
        const response = await nativeFetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) {
            if (response.status === 403) response.clone().json().then((b) => {
                if (b?.error === "account_blocked") showWall(b.reason);
                else if (b?.error === "account_deactivated") showBanner();
            }).catch(() => {});
            throw new Error(String(response.status));
        }
        return response.json();
    }

    function fillQuota(access) {
        const node = document.getElementById("nav-quota");
        if (!node) return;
        node.textContent = access.daily_limit == null
            ? "Admin: no daily limit"
            : `Aaj ki videos baqi: ${Math.max(0, access.daily_remaining)} / ${access.daily_limit}`;
    }

    // --- helpers shared by library / profile pages ---
    window.noorApi = async function noorApi(path, { method = "GET", json, body, headers = {} } = {}) {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) throw new Error("Please sign in again.");
        const response = await window.fetch(`${API}${path}`, {
            method,
            headers: {
                Authorization: `Bearer ${session.access_token}`,
                ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
                ...headers
            },
            body: json !== undefined ? JSON.stringify(json) : body
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(result.detail || `Request failed (HTTP ${response.status}).`);
            error.status = response.status;
            throw error;
        }
        return result;
    };
    window.noorApiOrigin = apiOrigin;

    window.noorConfirm = function noorConfirm(title, message, { label = "Confirm", danger = false } = {}) {
        return new Promise((resolve) => {
            const dialog = el("dialog", "confirm");
            dialog.setAttribute("aria-labelledby", "confirm-title");
            const heading = el("h3", "", title);
            heading.id = "confirm-title";
            const row = el("div", "pf-row");
            const cancel = el("button", "btn btn-outline", "Cancel");
            const ok = el("button", `btn ${danger ? "btn-danger" : "btn-primary"}`, label);
            cancel.type = ok.type = "button";
            row.append(cancel, ok);
            dialog.append(heading, el("p", "", message), row);
            const done = (value) => { dialog.close(); dialog.remove(); resolve(value); };
            cancel.addEventListener("click", () => done(false));
            ok.addEventListener("click", () => done(true));
            dialog.addEventListener("cancel", (event) => { event.preventDefault(); done(false); });
            document.body.append(dialog);
            dialog.showModal();
            cancel.focus();
        });
    };

    async function init() {
        const host = document.getElementById("site-header");
        if (!host) return;
        host.classList.add("site-header");
        const client = window.supabaseClient;
        let session = null;
        try {
            session = client ? (await client.auth.getSession()).data.session : null;
        } catch { /* treat as signed out */ }
        if (!session) {
            build(host, { signedIn: false });
            return;
        }
        // Render immediately with what the session knows, then upgrade with the server profile.
        const fallbackName = session.user.user_metadata?.full_name || (session.user.email || "").split("@")[0];
        build(host, { signedIn: true, profile: { display_name: fallbackName, email: session.user.email, is_admin: false } });
        try {
            const [profile, access] = await Promise.all([
                api("/me", session.access_token),
                api("/access", session.access_token).catch(() => null)
            ]);
            const wasOpen = ctl?.isOpen();
            document.getElementById("nav-drawer")?.remove();
            document.querySelector(".nav-backdrop")?.remove();
            build(host, { signedIn: true, profile });
            if (wasOpen) ctl.setOpen(true); // user opened the menu before the profile arrived
            if (access) fillQuota(access);
            window.dispatchEvent(new CustomEvent("noor:profile", { detail: profile }));
        } catch (error) {
            console.warn("Nav profile unavailable:", error.message);
        }
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
