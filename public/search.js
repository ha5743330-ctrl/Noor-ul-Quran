(function () {
    const form = document.getElementById("sr-form");
    const input = document.getElementById("sr-q");
    const status = document.getElementById("sr-status");
    const list = document.getElementById("sr-list");
    const base = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");

    const extra = document.getElementById("sr-extra");
    const TOPICS = ["patience", "mercy", "forgiveness", "prayer", "gratitude"];

    function topicChips(host) {
        const row = document.createElement("div");
        row.className = "st-topics";
        TOPICS.forEach((topic) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn btn-outline btn-sm";
            b.textContent = topic;
            b.addEventListener("click", () => { input.value = topic; submit(topic); });
            row.append(b);
        });
        host.append(row);
    }

    function firstView() {
        setStatus("empty", "");
        window.noorUi.empty(extra, { icon: "fa-magnifying-glass", title: "Ayat talash karein", text: "Arabic, Urdu ya English likhein, ya 2:255 jaisa reference.", action: { label: "Misaal: patience", onClick: () => { input.value = "patience"; submit("patience"); } } });
    }

    function setStatus(kind, text) {
        status.hidden = !text;
        status.dataset.kind = kind;
        status.textContent = text;
    }

    function line(cls, text, lang, dir) {
        const p = document.createElement("p");
        p.className = cls;
        p.textContent = text;
        if (lang) p.lang = lang;
        if (dir) p.dir = dir;
        return p;
    }

    function anchor(href, text, external) {
        const a = document.createElement("a");
        a.className = "btn btn-outline btn-sm";
        a.href = href;
        a.textContent = text;
        if (external) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
        return a;
    }

    function render(item) {
        const li = document.createElement("li");
        li.className = "ui-card sr-item";
        const head = document.createElement("p");
        head.className = "sr-ref";
        head.textContent = `${item.surah_name} ${item.ref}`;
        const actions = document.createElement("div");
        actions.className = "sr-actions";
        actions.append(anchor(item.generate_url, "Reel banayein"), anchor(item.tafsir_url, "Tafsir (quran.com)", true));
        li.append(head, line("sr-ar", item.arabic, "ar", "rtl"), line("sr-ur", item.urdu, "ur", "rtl"), line("sr-en", item.english, "en"), actions);
        return li;
    }

    async function run(q) {
        list.replaceChildren();
        setStatus("empty", "");
        window.noorUi.skeleton(extra, 3);
        try {
            const res = await fetch(`${base}/search?q=${encodeURIComponent(q)}`);
            if (!res.ok) { const e = new Error("bad status"); e.status = res.status; throw e; }
            const data = await res.json();
            extra.replaceChildren();
            if (!data.results.length) {
                const box = window.noorUi.empty(extra, { icon: "fa-magnifying-glass-minus", title: `"${q}" ke liye kuch nahi mila`, text: "Spelling check karein, ya in topics mein se koi try karein." });
                topicChips(box);
                return;
            }
            setStatus("empty", data.total > data.results.length ? `Showing ${data.results.length} of ${data.total} matches. Narrow your search to see others.` : `${data.total} match${data.total === 1 ? "" : "es"}.`);
            list.replaceChildren(...data.results.map(render));
        } catch (err) {
            window.noorUi.error(extra, { what: "Search", err, onRetry: () => run(q) });
        }
    }

    function submit(raw) {
        const q = raw.trim();
        if (q.length < 2) { window.noorUi.fieldError(input, "Kam az kam 2 harf likhein."); return; }
        window.noorUi.clearField(input);
        history.replaceState(null, "", `?q=${encodeURIComponent(q)}`);
        run(q);
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        submit(input.value);
    });

    const initial = new URLSearchParams(window.location.search).get("q");
    if (initial) { input.value = initial; run(initial.trim()); } else firstView();
})();
