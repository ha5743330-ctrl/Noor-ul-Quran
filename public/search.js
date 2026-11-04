(function () {
    const form = document.getElementById("sr-form");
    const input = document.getElementById("sr-q");
    const status = document.getElementById("sr-status");
    const list = document.getElementById("sr-list");
    const base = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");

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
        setStatus("loading", "Searching…");
        try {
            const res = await fetch(`${base}/search?q=${encodeURIComponent(q)}`);
            if (!res.ok) throw new Error("bad status");
            const data = await res.json();
            if (!data.results.length) { setStatus("empty", `No ayat found for “${q}”.`); return; }
            setStatus("empty", data.total > data.results.length ? `Showing ${data.results.length} of ${data.total} matches. Narrow your search to see others.` : `${data.total} match${data.total === 1 ? "" : "es"}.`);
            list.replaceChildren(...data.results.map(render));
        } catch {
            setStatus("error", "Search failed. Please try again.");
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const q = input.value.trim();
        if (q.length < 2) { setStatus("error", "Type at least 2 characters."); return; }
        history.replaceState(null, "", `?q=${encodeURIComponent(q)}`);
        run(q);
    });

    const initial = new URLSearchParams(window.location.search).get("q");
    if (initial) { input.value = initial; run(initial.trim()); }
})();
