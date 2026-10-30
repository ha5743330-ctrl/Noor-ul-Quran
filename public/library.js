// Library: all of the user's videos and ayah images. Mirrors the Recent list on the generate page.
const DIRECT_DOWNLOAD_LIMIT = 5; // more than this goes through one ZIP
const PAGE_SIZE = 50;
let shownCount = PAGE_SIZE;
let items = [];
let filter = "all";
let selecting = false;
const selected = new Set();

const $ = (id) => document.getElementById(id);

function setStatus(message, isError = true) {
    const node = $("lib-status");
    node.textContent = message;
    node.className = `pf-msg ${message ? (isError ? "err" : "ok") : ""}`;
}

function absolute(url) {
    return new URL(url, window.noorApiOrigin).href;
}

function visibleItems() {
    return items.filter((item) => filter === "all" || (item.type === "image" ? "image" : "video") === filter);
}

function updateBulk() {
    const shown = visibleItems();
    $("lib-bulk").classList.toggle("on", selecting);
    $("lib-select-toggle").hidden = selecting || !items.length;
    $("lib-count").textContent = `${selected.size} selected`;
    const all = $("lib-select-all");
    all.checked = shown.length > 0 && shown.every((item) => selected.has(item.id));
    all.indeterminate = !all.checked && shown.some((item) => selected.has(item.id));
    $("lib-download").disabled = !items.some((item) => selected.has(item.id) && item.has_file);
    $("lib-delete").disabled = selected.size === 0;
}

function button(label, className, onClick, disabled = false) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = `btn btn-sm ${className}`;
    node.textContent = label;
    node.disabled = disabled;
    node.addEventListener("click", onClick);
    return node;
}

function render() {
    const list = $("lib-list");
    const shown = visibleItems();
    list.replaceChildren();
    $("lib-empty").hidden = shown.length > 0;
    $("lib-more").hidden = shown.length <= shownCount;
    for (const item of shown.slice(0, shownCount)) {
        const isImage = item.type === "image";
        const li = document.createElement("li");
        li.className = "lib-item";

        if (selecting) {
            const label = document.createElement("label");
            label.className = "lib-check";
            const box = document.createElement("input");
            box.type = "checkbox";
            box.checked = selected.has(item.id);
            box.addEventListener("change", () => {
                if (box.checked) selected.add(item.id);
                else selected.delete(item.id);
                updateBulk();
            });
            label.append(box, document.createTextNode("Select"));
            li.append(label);
        }

        const thumb = document.createElement("div");
        thumb.className = "lib-thumb";
        if (isImage && item.thumb_url) {
            const img = document.createElement("img");
            img.src = absolute(item.thumb_url);
            img.alt = `Ayah image ${item.reference || ""}`;
            img.loading = "lazy";
            thumb.append(img);
        } else {
            const icon = document.createElement("i");
            icon.className = `fa-solid ${isImage ? "fa-image" : "fa-film"}`;
            icon.setAttribute("aria-hidden", "true");
            thumb.append(icon);
        }

        const title = document.createElement("p");
        title.className = "lib-title";
        if (isImage) {
            title.textContent = `Ayah ${item.reference || ""}`;
        } else {
            const range = item.start_ayah === item.end_ayah ? `${item.start_ayah ?? ""}` : `${item.start_ayah ?? ""}-${item.end_ayah ?? ""}`;
            title.textContent = `Surah ${item.surah ?? ""} · Ayat ${range}`;
        }
        const meta = document.createElement("p");
        meta.className = "lib-meta";
        const date = new Date(item.created_at);
        meta.textContent = `${isImage ? "Image" : "Video"} · ${Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString()}${item.has_file ? "" : " · Expire"}`;

        li.append(thumb, title, meta);
        if (!selecting) {
            const actions = document.createElement("div");
            actions.className = "lib-actions";
            actions.append(
                button(isImage ? "Open" : "Play", "btn-primary", () => openItem(item, "play"), !item.has_file),
                button("Download", "btn-outline", () => downloadOne(item), !item.has_file),
                button("Delete", "btn-danger-outline", () => deleteItems([item.id]))
            );
            li.append(actions);
        }
        list.append(li);
    }
    updateBulk();
}

async function itemUrl(item, purpose) {
    const result = await window.noorApi(`/history/${encodeURIComponent(item.id)}/url?purpose=${purpose}`);
    return absolute(result.url);
}

function triggerDownload(url) {
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
}

async function openItem(item, purpose) {
    try {
        window.open(await itemUrl(item, purpose), "_blank", "noopener");
        setStatus("");
    } catch (error) {
        setStatus(error.message);
    }
}

async function downloadOne(item) {
    try {
        triggerDownload(await itemUrl(item, "download"));
        setStatus("");
    } catch (error) {
        setStatus(error.message);
    }
}

async function downloadSelected() {
    const chosen = items.filter((item) => selected.has(item.id) && item.has_file);
    if (!chosen.length) return;
    try {
        if (chosen.length <= DIRECT_DOWNLOAD_LIMIT) {
            for (const item of chosen) {
                triggerDownload(await itemUrl(item, "download"));
                await new Promise((resolve) => setTimeout(resolve, 400));
            }
        } else {
            const result = await window.noorApi("/history/bulk-download-url", { method: "POST", json: { ids: chosen.map((item) => item.id) } });
            triggerDownload(absolute(result.url));
        }
        setSelecting(false);
        setStatus("");
    } catch (error) {
        setStatus(error.message);
    }
}

async function deleteItems(ids) {
    const ok = await window.noorConfirm(
        `Delete ${ids.length} ${ids.length === 1 ? "item" : "items"}?`,
        "This cannot be undone.",
        { label: "Delete", danger: true }
    );
    if (!ok) return;
    try {
        for (let start = 0; start < ids.length; start += 100) {
            await window.noorApi("/history/bulk-delete", { method: "POST", json: { ids: ids.slice(start, start + 100) } });
        }
        ids.forEach((id) => selected.delete(id));
        setSelecting(false);
        await load();
    } catch (error) {
        setStatus(error.message);
    }
}

function setSelecting(next) {
    selecting = next;
    selected.clear();
    render();
}

async function load() {
    try {
        const result = await window.noorApi("/history");
        items = (Array.isArray(result.videos) ? result.videos : [])
            .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
        setStatus("");
    } catch (error) {
        if (error.status === 401) {
            window.location.replace(`/auth.html?next=${encodeURIComponent("/library.html")}`);
            return;
        }
        setStatus(error.message);
    }
    render();
}

document.querySelectorAll(".lib-tabs button").forEach((tab) => {
    tab.addEventListener("click", () => {
        filter = tab.dataset.filter;
        shownCount = PAGE_SIZE;
        document.querySelectorAll(".lib-tabs button").forEach((other) => other.setAttribute("aria-pressed", String(other === tab)));
        render();
    });
});
$("lib-more").addEventListener("click", () => { shownCount += PAGE_SIZE; render(); });
$("lib-select-toggle").addEventListener("click", () => setSelecting(true));
$("lib-cancel").addEventListener("click", () => setSelecting(false));
$("lib-select-all").addEventListener("change", (event) => {
    visibleItems().forEach((item) => (event.target.checked ? selected.add(item.id) : selected.delete(item.id)));
    render();
});
$("lib-download").addEventListener("click", downloadSelected);
$("lib-delete").addEventListener("click", () => deleteItems([...selected]));

document.addEventListener("DOMContentLoaded", async () => {
    const { data: { session } } = window.supabaseClient
        ? await window.supabaseClient.auth.getSession()
        : { data: { session: null } };
    if (!session) {
        window.location.replace(`/auth.html?next=${encodeURIComponent("/library.html")}`);
        return;
    }
    load();
});
