// Studio UI glue only: tab switch + colored mode tiles. Generation logic stays in app.js.
(function () {
    const form = document.getElementById("reel-generator-form");
    const sel = document.getElementById("input-content-mode");
    if (!form) return;
    const setTab = (tab) => {
        form.dataset.tab = tab;
        document.querySelectorAll(".studio-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
        try { history.replaceState(null, "", tab === "image" ? "#image" : location.pathname + location.search); } catch {}
    };
    document.querySelectorAll(".studio-tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
    if (location.hash === "#image") setTab("image");
    if (!sel) return;
    const sync = () => document.querySelectorAll("#mode-tiles button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cm === sel.value)));
    document.querySelectorAll("#mode-tiles button").forEach((b) => b.addEventListener("click", () => {
        sel.value = b.dataset.cm;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sync();
    }));
    sel.addEventListener("change", sync);
    sync();
})();
