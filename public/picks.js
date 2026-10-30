// Managed Picks panel (generate page, "Picks" mode) + the "This batch" card.
// app.js calls: NoorPicks.show() from setMode, NoorPicks.batchRequest() from generateVideo,
// NoorPicks.onJob(job) while a job is polled. All server text goes through textContent.
(function () {
    "use strict";
    const API = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
    const BUCKETS = [[30, "30 sec"], [60, "1 min"], [120, "2 min"]];
    const MODES = [["urdu", "Urdu-only"], ["arabic", "Arabic-only"]];
    const STORE_KEY = "noor.picks.selection.v1";
    const MAX_BATCH = 25;
    const RENDER_RATE = 3; // rough render seconds per video second, for the estimate only
    const DIRECT_LIMIT = 5;

    const state = {
        bucket: 30, mode: null, cap: 35, search: "", data: {}, sel: {}, batch: null, batchSel: new Set(), batchSeen: new Set(), busy: false,
    };
    let root = null;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function loadStore() {
        try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
    }

    function saveStore() {
        const out = {};
        Object.entries(state.sel).forEach(([key, set]) => { out[key] = [...set]; });
        try { localStorage.setItem(STORE_KEY, JSON.stringify(out)); } catch { /* storage blocked */ }
    }

    function defaultMode() {
        return document.getElementById("input-content-mode")?.value === "arabic_only" ? "arabic" : "urdu";
    }

    async function token() {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) throw new Error("Sign in again to use Picks.");
        return session.access_token;
    }

    async function getPicks() {
        const response = await fetch(`${API}/picks?bucket=${state.bucket}&mode=${state.mode}`, {
            headers: { Authorization: `Bearer ${await token()}` },
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || `Could not load picks (HTTP ${response.status}).`);
        return body;
    }

    const key = () => `${state.bucket}:${state.mode}`;
    const rows = () => state.data[key()]?.picks || [];
    const fits = (row) => row.selectable && !row.over_bucket;
    const mmss = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

    function selection() {
        return state.sel[key()];
    }

    function visibleRows() {
        const term = state.search.trim().toLowerCase();
        return rows().filter((row) => !term || row.ref.toLowerCase().includes(term) || row.surah_name.toLowerCase().includes(term));
    }

    async function ensureLoaded() {
        if (!state.data[key()]) {
            const body = await getPicks();
            state.data[key()] = body;
            state.cap = body.cap;
            const ok = new Set(body.picks.filter((row) => row.selectable).map((row) => row.id));
            const saved = loadStore()[key()];
            if (Array.isArray(saved)) {
                state.sel[key()] = new Set(saved.filter((id) => ok.has(id)).slice(0, MAX_BATCH));
            } else if (state.bucket === 30) { // default view: fitting picks of the 30 s tab preselected
                state.sel[key()] = new Set(body.picks.filter(fits).slice(0, MAX_BATCH).map((row) => row.id));
            } else {
                state.sel[key()] = new Set();
            }
        }
    }

    async function refresh() {
        const list = root.querySelector(".pk-list");
        list.replaceChildren(el("p", "pk-empty", "Loading picks…"));
        try {
            await ensureLoaded();
        } catch (error) {
            list.replaceChildren(el("p", "pk-empty pk-err", error.message));
            return;
        }
        renderList();
    }

    function chip(label, active, onClick, className) {
        const button = el("button", `pk-chip ${className || ""}`, label);
        button.type = "button";
        button.setAttribute("aria-pressed", String(active));
        button.addEventListener("click", onClick);
        return button;
    }

    function build() {
        root.replaceChildren();
        root.className = "pk";
        const tabs = el("div", "pk-chips");
        tabs.setAttribute("role", "group");
        tabs.setAttribute("aria-label", "Length");
        BUCKETS.forEach(([bucket, label]) => tabs.append(chip(label, state.bucket === bucket, () => { state.bucket = bucket; build(); refresh(); })));
        const modes = el("div", "pk-chips");
        modes.setAttribute("role", "group");
        modes.setAttribute("aria-label", "Language");
        MODES.forEach(([mode, label]) => modes.append(chip(label, state.mode === mode, () => { state.mode = mode; build(); refresh(); })));

        const search = el("input", "control pk-search");
        search.type = "search";
        search.placeholder = "Search surah or 2:255";
        search.setAttribute("aria-label", "Search picks");
        search.value = state.search;
        search.addEventListener("input", () => { state.search = search.value; renderList(); });

        const actions = el("div", "pk-actions");
        [["Select all fitting", selectFitting], ["Clear", clearSel], ["Invert", invertSel]].forEach(([label, fn]) => {
            const button = el("button", "btn btn-outline btn-sm", label);
            button.type = "button";
            button.addEventListener("click", fn);
            actions.append(button);
        });

        const summary = el("p", "pk-summary");
        summary.id = "picks-summary";
        summary.setAttribute("role", "status");
        const msg = el("p", "pk-msg");
        msg.id = "picks-msg";
        msg.setAttribute("role", "alert");
        root.append(tabs, modes, search, actions, summary, msg, el("ul", "pk-list"));
    }

    function renderList() {
        const list = root.querySelector(".pk-list");
        const chosen = selection();
        list.replaceChildren();
        const shown = visibleRows();
        if (!shown.length) list.append(el("li", "pk-empty", rows().length ? "No picks match your search." : "No picks in this list yet."));
        shown.forEach((row) => {
            const item = el("li", `pk-row${row.selectable ? "" : " pk-off"}`);
            const label = el("label", "pk-label");
            const box = el("input");
            box.type = "checkbox";
            box.checked = chosen.has(row.id);
            box.disabled = !row.selectable;
            box.addEventListener("change", () => toggle(row.id, box));
            const main = el("span", "pk-main");
            main.append(el("strong", "pk-ref", row.ref), el("span", "pk-name", row.surah_name));
            const meta = el("span", "pk-meta");
            meta.append(el("span", "pk-secs", row.seconds == null ? "? s" : `${row.seconds.toFixed(1)} s`));
            if (!row.selectable) meta.append(el("span", "pk-badge pk-bad", `Over ${state.cap} s cap — use the local app`));
            else if (row.over_bucket) meta.append(el("span", "pk-badge pk-warn", `Over ${state.bucket} s`));
            else meta.append(el("span", "pk-badge pk-ok", "Fits"));
            label.append(box, main, meta);
            item.append(label);
            list.append(item);
        });
        updateSummary();
    }

    function updateSummary() {
        const chosen = selection();
        const picked = rows().filter((row) => chosen.has(row.id));
        const seconds = picked.reduce((sum, row) => sum + (row.seconds || 0), 0);
        const summary = document.getElementById("picks-summary");
        if (summary) {
            summary.textContent = `Selected ${picked.length} | est. render time ${mmss(seconds * RENDER_RATE)} | total length ${mmss(seconds)}`;
        }
        const access = window.userAccess || {};
        const over = picked.length > MAX_BATCH ? `At most ${MAX_BATCH} per batch.`
            : access.daily_remaining != null && picked.length > access.daily_remaining
                ? `You have ${Math.max(0, access.daily_remaining)} videos left today; untick some picks.` : "";
        setMsg(over);
    }

    function setMsg(text) {
        const node = document.getElementById("picks-msg");
        if (node) node.textContent = text;
    }

    function toggle(id, box) {
        const chosen = selection();
        if (box.checked && chosen.size >= MAX_BATCH) {
            box.checked = false;
            setMsg(`At most ${MAX_BATCH} per batch.`);
            return;
        }
        if (box.checked) chosen.add(id); else chosen.delete(id);
        saveStore();
        updateSummary();
    }

    function selectFitting() {
        const chosen = selection();
        chosen.clear();
        visibleRows().filter(fits).slice(0, MAX_BATCH).forEach((row) => chosen.add(row.id));
        saveStore();
        renderList();
    }

    function clearSel() {
        selection().clear();
        saveStore();
        renderList();
    }

    function invertSel() {
        const chosen = selection();
        visibleRows().filter((row) => row.selectable).forEach((row) => {
            if (chosen.has(row.id)) chosen.delete(row.id);
            else if (chosen.size < MAX_BATCH) chosen.add(row.id);
        });
        saveStore();
        renderList();
    }

    // ---- hooks used by app.js ----
    function show() {
        root = document.getElementById("picks-root");
        if (!root) return;
        if (!state.mode) state.mode = defaultMode();
        if (!root.querySelector(".pk-list")) build();
        refresh();
    }

    function batchRequest() {
        const chosen = selection();
        if (!chosen || !chosen.size) { setMsg("Tick at least one pick."); return null; }
        const access = window.userAccess || {};
        if (access.daily_remaining != null && chosen.size > access.daily_remaining) {
            setMsg(`You have ${Math.max(0, access.daily_remaining)} videos left today; untick some picks.`);
            return null;
        }
        setMsg("");
        const arabic = state.mode === "arabic";
        return {
            mode: "picks", pick_ids: [...chosen], verse: "", content_mode: arabic ? "arabic_only" : "urdu_only",
            reciter_id: null, urdu_voice_id: null, ...(arabic ? { music: null } : {}),
        };
    }

    // ---- "This batch" card ----
    const STATUS_LABEL = { queued: "Queued", rendering: "Rendering", done: "Done", failed: "Failed" };

    function onJob(job) {
        const card = document.getElementById("picks-batch");
        if (!card || !Array.isArray(job.items) || !job.items.some((item) => item.pick_id)) return;
        if (!state.batch || state.batch.job_id !== job.job_id) { state.batchSel = new Set(); state.batchSeen = new Set(); }
        state.batch = job;
        job.items.forEach((item) => { // finished videos start ticked, once
            if (item.status === "done" && item.history_id && !state.batchSeen.has(item.history_id)) {
                state.batchSeen.add(item.history_id);
                state.batchSel.add(item.history_id);
            }
        });
        renderBatch();
        if (["completed", "partial", "failed"].includes(job.status)) refreshAccess();
    }

    async function refreshAccess() {
        try {
            const response = await fetch(`${API}/access`, { headers: { Authorization: `Bearer ${await token()}` } });
            if (response.ok) {
                window.userAccess = await response.json();
                if (typeof applyFreeTierLimits === "function") applyFreeTierLimits();
                if (root) updateSummary();
            }
        } catch { /* the next page load refreshes it */ }
    }

    function renderBatch() {
        const card = document.getElementById("picks-batch");
        const job = state.batch;
        card.hidden = !job;
        if (!job) return;
        card.replaceChildren();
        const done = job.items.filter((item) => item.status === "done" && item.history_id);
        const failed = job.items.filter((item) => item.status === "failed").length;
        card.append(el("h3", "pk-batch-title", "This batch"));
        card.append(el("p", "pk-batch-sub",
            `${done.length} of ${job.items.length} ready${failed ? ` · ${failed} failed (slot refunded)` : ""}${job.status === "queued" && job.queue_ahead != null ? ` · ${job.queue_ahead} video(s) ahead` : ""}`));
        const list = el("ul", "pk-batch-list");
        job.items.forEach((item) => {
            const row = el("li", `pk-brow pk-${item.status}`);
            const label = el("label", "pk-label");
            const box = el("input");
            box.type = "checkbox";
            box.disabled = !(item.status === "done" && item.history_id);
            box.checked = box.disabled ? false : state.batchSel.has(item.history_id);
            box.addEventListener("change", () => {
                if (box.checked) state.batchSel.add(item.history_id); else state.batchSel.delete(item.history_id);
                renderBatch();
            });
            const main = el("span", "pk-main");
            main.append(el("strong", "pk-ref", item.ref || `#${item.index}`));
            const meta = el("span", "pk-meta");
            meta.append(el("span", `pk-badge pk-st-${item.status}`, STATUS_LABEL[item.status] || item.status));
            if (item.reason) meta.append(el("span", "pk-reason", item.reason));
            if (item.warning) meta.append(el("span", "pk-reason", item.warning));
            label.append(box, main, meta);
            row.append(label);
            list.append(row);
        });
        card.append(list);
        const picked = done.filter((item) => state.batchSel.has(item.history_id));
        const bar = el("div", "pk-actions");
        const sel = el("button", "btn btn-primary btn-sm", `Download selected (${picked.length})`);
        sel.type = "button";
        sel.disabled = !picked.length || state.busy;
        sel.addEventListener("click", () => download(picked.map((item) => item.history_id)));
        const all = el("button", "btn btn-outline btn-sm", "Download all from this batch");
        all.type = "button";
        all.disabled = !done.length || state.busy;
        all.addEventListener("click", () => download(done.map((item) => item.history_id)));
        bar.append(sel, all);
        const msg = el("p", "pk-msg");
        msg.id = "picks-batch-msg";
        msg.setAttribute("role", "status");
        card.append(bar, msg);
    }

    function batchMsg(text) {
        const node = document.getElementById("picks-batch-msg");
        if (node) node.textContent = text;
    }

    async function download(ids) {
        if (!ids.length || state.busy) return;
        state.busy = true;
        renderBatch();
        try {
            if (ids.length <= DIRECT_LIMIT) {
                for (const id of ids) {
                    triggerDownload(await requestRecentVideoUrl({ id }, "download"));
                    await new Promise((resolve) => setTimeout(resolve, 400));
                }
            } else {
                const result = await historyRequest("/history/bulk-download-url", { ids });
                triggerDownload(new URL(result.url, new URL(API, window.location.origin).origin).href);
                if (result.missing?.length) batchMsg(`${result.missing.length} video(s) are no longer available and are listed in MISSING.txt: ${result.missing.join(", ")}`);
            }
        } catch (error) {
            batchMsg(error.message || "Could not download these videos.");
        } finally {
            state.busy = false;
            renderBatch();
        }
    }

    window.NoorPicks = { show, batchRequest, onJob };
})();
