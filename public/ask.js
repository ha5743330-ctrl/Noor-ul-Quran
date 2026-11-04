(function () {
    const form = document.getElementById("ask-form");
    const input = document.getElementById("ask-q");
    const go = document.getElementById("ask-go");
    const status = document.getElementById("ask-status");
    const answer = document.getElementById("ask-answer");
    const list = document.getElementById("ask-ayat");
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

    function ayahCard(a) {
        const li = document.createElement("li");
        li.className = "ui-card sr-item";
        li.id = `ayah-${a.ref.replace(":", "-")}`;
        const actions = document.createElement("div");
        actions.className = "sr-actions";
        const make = document.createElement("a");
        make.className = "btn btn-outline btn-sm";
        make.href = a.generate_url;
        make.textContent = "Make video";
        actions.append(make);
        li.append(line("sr-ref", `${a.surah_name} ${a.ref}`), line("sr-ar", a.arabic, "ar", "rtl"), line("sr-ur", a.urdu, "ur", "rtl"), line("sr-en", a.english, "en"), actions);
        return li;
    }

    // [2:153] becomes a link to the ayah card below; all text goes through textContent
    function renderAnswer(text) {
        const p = document.createElement("p");
        text.split(/(\[\d{1,3}:\d{1,3}\])/).forEach((part) => {
            const m = part.match(/^\[(\d{1,3}):(\d{1,3})\]$/);
            if (!m) { p.append(part); return; }
            const a = document.createElement("a");
            a.href = `#ayah-${m[1]}-${m[2]}`;
            a.className = "ask-cite";
            a.textContent = `${m[1]}:${m[2]}`;
            p.append(a);
        });
        answer.replaceChildren(p);
        answer.hidden = false;
    }

    function needSignIn() {
        const a = document.createElement("a");
        a.href = `/auth.html?next=${encodeURIComponent("/ask.html")}`;
        a.textContent = "sign in";
        status.replaceChildren("Please ", a, " to ask a question.");
        status.dataset.kind = "empty";
        status.hidden = false;
    }

    async function ask(q) {
        answer.hidden = true;
        list.replaceChildren();
        go.disabled = true;
        setStatus("loading", "Thinking…");
        try {
            const { data: { session } } = await window.supabaseClient.auth.getSession();
            if (!session) { needSignIn(); return; }
            const res = await fetch(`${base}/ask`, {
                method: "POST",
                headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
                body: JSON.stringify({ question: q })
            });
            if (res.status === 429) { setStatus("error", "Too many questions in a row. Wait a minute."); return; }
            if (!res.ok) throw new Error("bad status");
            const data = await res.json();
            if (data.status === "ok") { renderAnswer(data.answer); setStatus("empty", ""); }
            else setStatus(data.status === "limit" ? "error" : "empty", data.message);
            list.replaceChildren(...data.ayat.map(ayahCard));
        } catch {
            setStatus("error", "Something went wrong. Please try again.");
        } finally {
            go.disabled = false;
        }
    }

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (q.length >= 3) ask(q);
    });
    document.querySelectorAll(".ask-chips button").forEach((b) => b.addEventListener("click", () => { input.value = b.textContent; ask(b.textContent); }));
})();
