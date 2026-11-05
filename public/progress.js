// Waiting card: shown in the Results area while a video/image generates. window.NoorWait.{start,update,stop}.
(() => {
    const API = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
    const STAGES = [["fa-volume-high", "Awaz", "Awaz tayyar ho rahi hai"], ["fa-font", "Alfaz", "Alfaz likhe ja rahe hain"],
        ["fa-images", "Frames", "Frames ban rahe hain"], ["fa-film", "Video", "Video jud rahi hai"]];
    // Patience/ease ayat only; Arabic + translation are read from /api/search (the app's own Quran data).
    const VERSES = [[[94, 5], [94, 6]], [[2, 153]], [[39, 10]], [[65, 3]]];
    const cache = {};
    let card, st = null, timers = [];

    const verse = (s, a) => (cache[`${s}:${a}`] ||= fetch(`${API}/search?q=${s}:${a}`).then((r) => r.json())
        .then((d) => d.results?.[0] || null).catch(() => null));
    const el = (cls, tag = "div") => Object.assign(document.createElement(tag), { className: cls });
    const q = (c) => card.querySelector(c);
    const setTr = (node, v) => {
        node.textContent = st.lang === "en" ? v.english : v.urdu;
        node.lang = st.lang;
        node.dir = st.lang === "en" ? "ltr" : "rtl";
    };

    function build() {
        card = el("wc-card");
        card.id = "waiting-card";
        card.hidden = true;
        card.innerHTML = `<div class="wc-top"><ol class="wc-stages">${STAGES.map(([i, n]) =>
            `<li><i class="fa-solid ${i}" aria-hidden="true"></i><span>${n}</span></li>`).join("")}</ol>
            <span class="wc-wave" aria-hidden="true"><b></b><b></b><b></b><b></b><b></b></span></div>
            <p class="wc-status" role="status" aria-live="polite"></p>
            <div class="wc-peek" hidden aria-hidden="true"><span>Image ban rahi hai</span></div>
            <div class="wc-reveal"><p class="ar-text wc-ar" dir="rtl" lang="ar"></p><p class="wc-tr"></p></div>
            <div class="wc-verse" aria-live="off"><p class="ar-text wc-vq" dir="rtl" lang="ar"></p><p class="wc-vt"></p><p class="wc-vr"></p></div>`;
        document.getElementById("empty-results").before(card);
    }

    function paint() {
        const pct = Math.round(st.pct);
        card.querySelectorAll(".wc-stages li").forEach((li, i) => {
            li.classList.toggle("on", i <= st.stage);
            li.classList.toggle("now", i === st.stage);
        });
        const label = st.kind === "image" ? "Image ban rahi hai" : st.queued ? "Queue mein hai, thori der" : STAGES[st.stage][2];
        const status = q(".wc-status");
        if (status.dataset.k !== label) { status.dataset.k = label; status.textContent = `${label}...`; }
        const words = [...q(".wc-ar").children], n = Math.ceil((pct / 100) * words.length);
        words.forEach((w, i) => w.classList.toggle("on", i < n));
        q(".wc-tr").classList.toggle("on", n > 0);
    }

    async function loadRef(ref) {
        const m = /^(\d{1,3}):(\d{1,3})/.exec(ref || "");
        if (!m || st.ref === m[0]) return;
        st.ref = m[0];
        const v = await verse(+m[1], +m[2]);
        if (!v || !st || st.ref !== m[0]) return;
        q(".wc-ar").replaceChildren(...v.arabic.split(/\s+/).slice(0, 40).map((w) =>
            Object.assign(el("wc-w", "span"), { textContent: w + " " })));
        setTr(q(".wc-tr"), v);
        paint();
    }

    async function rotate() {
        const group = VERSES[st.v++ % VERSES.length];
        const rows = await Promise.all(group.map(([s, a]) => verse(s, a)));
        if (!st || rows.some((r) => !r)) return;
        const box = q(".wc-verse");
        box.classList.remove("on");
        setTimeout(() => {
            if (!st) return;
            q(".wc-vq").textContent = rows.map((r) => r.arabic).join(" ");
            setTr(q(".wc-vt"), { english: rows.map((r) => r.english).join(" "), urdu: rows.map((r) => r.urdu).join(" ") });
            const [s, a] = group[0], last = group[group.length - 1][1];
            q(".wc-vr").textContent = last === a ? `${s}:${a}` : `${s}:${a}-${last}`;
            box.classList.add("on");
        }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 350);
    }

    window.NoorWait = {
        // opts: {kind: "video"|"image", ref, lang: "en"|"ur", style}
        start(opts) {
            this.stop();
            if (!card || !card.isConnected) build();
            st = { kind: opts.kind, lang: opts.lang === "en" ? "en" : "ur", pct: 0, stage: 0, v: 0, t0: Date.now(), ref: null };
            const image = st.kind === "image";
            q(".wc-peek").hidden = !image;
            q(".wc-peek").className = `wc-peek wc-peek--${opts.style || "emerald_gold"}`;
            card.classList.toggle("is-image", image);
            q(".wc-ar").replaceChildren();
            q(".wc-tr").textContent = "";
            q(".wc-tr").classList.remove("on");
            q(".wc-status").dataset.k = "";
            card.hidden = false;
            document.getElementById("empty-results")?.classList.add("hidden");
            paint();
            loadRef(opts.ref);
            rotate();
            timers.push(setInterval(rotate, 8000));
            // Images have no job polling: ease toward 95% over ~12 s.
            if (image) timers.push(setInterval(() => {
                st.pct = 95 * (1 - Math.exp(-(Date.now() - st.t0) / 6000));
                st.stage = 3;
                paint();
            }, 400));
        },
        // job = GET /generate/{id} body. Uses job.percent / job.stage if the server sends them, else estimated_wait_seconds.
        update(job) {
            if (!st || st.kind === "image" || !job) return;
            st.queued = job.status === "queued";
            if (st.queued) st.t0 = Date.now();
            loadRef(job.items?.[0]?.ref);
            const est = Number(job.estimated_wait_seconds), ms = Date.now() - st.t0;
            const done = job.items?.length ? job.items.filter((i) => i.status === "done").length / job.items.length * 100 : 0;
            const pct = Number.isFinite(job.percent) ? job.percent : st.queued ? 0 : est > 0 ? ms / (ms + est * 1000) * 100 : st.pct;
            st.pct = Math.min(95, Math.max(st.pct, pct, done));
            const named = { audio: 0, text: 1, frames: 2, video: 3 }[job.stage];
            st.stage = named ?? (st.queued ? 0 : Math.min(3, Math.floor(st.pct / 25)));
            paint();
        },
        stop() {
            timers.forEach(clearInterval);
            timers = [];
            st = null;
            if (!card) return;
            card.hidden = true;
            if (document.getElementById("video-preview-box")?.classList.contains("hidden") &&
                document.getElementById("image-result-box")?.classList.contains("hidden")) {
                document.getElementById("empty-results")?.classList.remove("hidden");
            }
        },
    };
})();
