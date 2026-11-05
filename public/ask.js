(function () {
    const form = document.getElementById("ask-form");
    const input = document.getElementById("ask-q");
    const go = document.getElementById("ask-go");
    const status = document.getElementById("ask-status");
    const answer = document.getElementById("ask-answer");
    const list = document.getElementById("ask-ayat");
    const base = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");

    const extra = document.getElementById("ask-extra");
    const TOPICS = ["What does the Quran say about patience?", "How should we treat parents?", "What is said about forgiveness?", "What is said about gratitude?", "What does the Quran say about prayer?"];
    let lastQuestion = "";

    function topicChips(host) {
        const row = document.createElement("div");
        row.className = "st-topics";
        TOPICS.forEach((topic) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn btn-outline btn-sm";
            b.textContent = topic;
            b.addEventListener("click", () => { input.value = topic; ask(topic); });
            row.append(b);
        });
        host.append(row);
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
        extra.replaceChildren();
    }

    async function ask(q) {
        answer.hidden = true;
        list.replaceChildren();
        go.disabled = true;
        lastQuestion = q;
        setStatus("empty", "");
        window.noorUi.skeleton(extra, 4, "text");
        try {
            const { data: { session } } = await window.supabaseClient.auth.getSession();
            if (!session) { needSignIn(); return; }
            const res = await fetch(`${base}/ask`, {
                method: "POST",
                headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
                body: JSON.stringify({ question: q })
            });
            if (res.status === 429) {
                extra.replaceChildren();
                setStatus("error", "Too many questions in a row. Wait a minute.");
                return;
            }
            if (!res.ok) { const e = new Error("bad status"); e.status = res.status; throw e; }
            const data = await res.json();
            extra.replaceChildren();
            if (data.status === "ok") { renderAnswer(data.answer); setStatus("empty", ""); }
            else {
                setStatus(data.status === "limit" ? "error" : "empty", data.message);
                if (data.status === "limit") {
                    window.noorUi.empty(extra, { icon: "fa-hourglass-half", title: "Aaj ki Ask AI limit poori ho gayi", text: "Kal dobara poochein. Tab tak ayat search kar sakte hain.", action: { label: "Ayat search karein", href: "/search.html" } });
                } else if (data.status !== "refused" && !data.ayat.length) {
                    topicChips(window.noorUi.empty(extra, { icon: "fa-face-meh", title: "Is sawal ka jawab nahi mila", text: "Sawal thora badal kar poochein, ya in mein se koi chunein." }));
                }
            }
            list.replaceChildren(...data.ayat.map(ayahCard));
        } catch (err) {
            setStatus("error", "Something went wrong. Please try again.");
            window.noorUi.error(extra, { what: "Jawab", err, onRetry: () => ask(lastQuestion) });
        } finally {
            go.disabled = false;
        }
    }

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (q.length < 3) { window.noorUi.fieldError(input, "Kam az kam 3 harf ka sawal likhein."); return; }
        window.noorUi.clearField(input);
        ask(q);
    });
    document.querySelectorAll(".ask-chips button").forEach((b) => b.addEventListener("click", () => { input.value = b.textContent; ask(b.textContent); }));
})();
