const adminMessage = document.getElementById("admin-message");
const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
let adminStats = { total: 0, premium: 0 };
let selfId = "";
let usersPage = 1;
let usersTotal = 0;
let usersRows = [];

function showAdminMessage(message, isError = false) {
    adminMessage.textContent = message;
    adminMessage.className = `ad-msg ${isError ? "err" : "ok"}`;
    adminMessage.hidden = false;
}

async function adminRequest(path, options = {}) {
    const { data: { session } } = await window.supabaseClient.auth.getSession();
    if (!session) throw new Error("Your session expired. Please sign in again.");

    const response = await fetch(`${API_BASE_URL}${path}`, {
        ...options,
        headers: {
            Authorization: `Bearer ${session.access_token}`,
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...options.headers
        }
    });
    const text = response.status === 204 ? "" : await response.text();
    let result = null;
    if (text) {
        try {
            result = JSON.parse(text);
        } catch {
            result = { detail: text };
        }
    }
    if (!response.ok) throw new Error(result?.detail || "Admin request failed.");
    return result;
}

async function initializeAdmin() {
    const client = window.supabaseClient;
    if (!client) {
        window.location.replace("/auth.html");
        return;
    }

    const { data: { session } } = await client.auth.getSession();
    if (!session) {
        window.location.replace("/auth.html");
        return;
    }

    try {
        const access = await adminRequest("/access");
        if (!access.is_admin) {
            window.location.replace("/");
            return;
        }
        selfId = access.user_id;
        await loadUsers();
        await loadAudit();
        await loadBackgrounds();
        await loadMusic();
        await loadDiagnostics();
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

function renderSummary() {
    const totalUsersStat = document.getElementById("stat-total-users");
    const premiumUsersStat = document.getElementById("stat-premium-users");
    const backgroundsBadge = document.getElementById("stat-backgrounds-badge");
    const backgroundCount = document.getElementById("stat-backgrounds");

    if (totalUsersStat) totalUsersStat.textContent = String(adminStats.total);
    if (premiumUsersStat) premiumUsersStat.textContent = String(adminStats.premium);
    if (backgroundCount) backgroundCount.textContent = String(backgroundCount.dataset.count || "0");
    if (backgroundsBadge) backgroundsBadge.textContent = `${backgroundCount?.dataset.count || 0} files`;
}

// Fetches pages from..usersPage: a refresh keeps everything already loaded, Load more only asks for the new page.
async function loadUsers(from = 1) {
    const list = document.getElementById("users-list");
    const rows = from === 1 ? [] : usersRows;
    try {
        for (let page = from; page <= usersPage; page += 1) {
            const params = new URLSearchParams({
                q: document.getElementById("user-search").value.trim(),
                status: document.getElementById("user-status").value,
                page: String(page)
            });
            const result = await adminRequest(`/admin/users?${params}`);
            adminStats = result.stats;
            usersTotal = result.total;
            rows.push(...result.users);
        }
        usersRows = rows;
        renderSummary();
        renderUsers(rows);
    } catch (error) {
        list.replaceChildren();
        showAdminMessage(error.message, true);
    }
}

function formatWhen(value) {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : "—";
}

function userFact(label, value) {
    const box = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    box.append(term, detail);
    return box;
}

function actionButton(label, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn btn-sm ${className}`;
    button.textContent = label;
    button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            await onClick();
        } catch (error) {
            showAdminMessage(error.message, true);
        } finally {
            button.disabled = false;
        }
    });
    return button;
}

function askReason(email) {
    return new Promise((resolve) => {
        const dialog = document.createElement("dialog");
        dialog.className = "confirm";
        const title = document.createElement("h3");
        title.textContent = `Block ${email}?`;
        const label = document.createElement("label");
        label.textContent = "Reason (optional, user ko dikhayi jayegi)";
        label.htmlFor = "block-reason";
        const input = document.createElement("textarea");
        input.id = "block-reason";
        input.maxLength = 200;
        input.rows = 3;
        input.className = "control";
        const row = document.createElement("div");
        row.className = "pf-row";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "btn btn-outline";
        cancel.textContent = "Cancel";
        const ok = document.createElement("button");
        ok.type = "button";
        ok.className = "btn btn-danger";
        ok.textContent = "Block";
        row.append(cancel, ok);
        dialog.append(title, label, input, row);
        const done = (value) => { cancel.disabled = ok.disabled = true; dialog.close(); dialog.remove(); resolve(value); };
        cancel.addEventListener("click", () => done(null));
        ok.addEventListener("click", () => done(input.value.trim()));
        dialog.addEventListener("cancel", (event) => { event.preventDefault(); done(null); });
        document.body.append(dialog);
        dialog.showModal();
        input.focus();
    });
}

async function userAction(user, action, body) {
    await adminRequest(`/admin/users/${encodeURIComponent(user.id)}/${action}`, {
        method: "POST",
        ...(body ? { body: JSON.stringify(body) } : {})
    });
    await Promise.all([loadUsers(), loadAudit()]);
}

function renderUsers(users) {
    const list = document.getElementById("users-list");
    list.replaceChildren();
    document.getElementById("user-count").textContent = `${usersTotal} users`;
    document.getElementById("users-badge").textContent = String(usersTotal);
    document.getElementById("users-more").hidden = users.length >= usersTotal;

    if (!users.length) {
        const empty = document.createElement("li");
        empty.className = "lib-empty";
        empty.textContent = "No users found.";
        list.append(empty);
        return;
    }

    for (const user of users) {
        const item = document.createElement("li");
        item.className = "au-item";

        const head = document.createElement("div");
        head.className = "au-head";
        const avatar = document.createElement("span");
        avatar.className = "nav-avatar nav-avatar-md";
        if (user.avatar_url) {
            const img = document.createElement("img");
            img.alt = "";
            img.src = user.avatar_url;
            avatar.append(img);
        } else {
            avatar.textContent = (user.display_name || user.email || "?").charAt(0).toUpperCase();
        }
        const who = document.createElement("div");
        who.className = "au-who";
        const name = document.createElement("strong");
        name.textContent = user.display_name || "No display name";
        const email = document.createElement("small");
        email.textContent = user.email;
        who.append(name, email);
        const status = document.createElement("span");
        status.className = `pf-badge au-status ${user.status === "blocked" ? "bad" : user.status === "deactivated" ? "warn" : ""}`;
        status.textContent = user.status;
        head.append(avatar, who, status);

        const facts = document.createElement("dl");
        facts.className = "au-facts";
        facts.append(
            userFact("Created", formatWhen(user.created_at)),
            userFact("Last sign-in", formatWhen(user.last_sign_in_at)),
            userFact("Videos today", String(user.videos_today)),
            userFact("Storage", `${(user.storage_bytes / (1024 * 1024)).toFixed(1)} MB`)
        );
        if (user.reason) facts.append(userFact("Block reason", user.reason));

        const actions = document.createElement("div");
        actions.className = "au-actions";
        const premium = document.createElement("label");
        premium.className = "lib-check";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = Boolean(user.premium_access);
        checkbox.disabled = user.is_admin;
        checkbox.setAttribute("aria-label", `Premium access for ${user.email}`);
        checkbox.addEventListener("change", () => updatePremium(user, checkbox));
        premium.append(checkbox, document.createTextNode("Premium"));
        actions.append(premium);
        if (user.status === "blocked") {
            actions.append(actionButton("Unblock", "btn-outline", () => userAction(user, "unblock")));
        } else if (user.id !== selfId) {
            actions.append(actionButton("Block", "btn-danger-outline", async () => {
                const reason = await askReason(user.email);
                if (reason !== null) await userAction(user, "block", { reason });
            }));
        }
        actions.append(
            actionButton("Sign out everywhere", "btn-outline", () => userAction(user, "signout")),
            actionButton("Delete content", "btn-danger-outline au-wide", async () => {
                const ok = await window.noorConfirm(`Delete all content of ${user.email}?`, "Saari videos aur images delete hongi. Yeh wapas nahi ho sakta.", { label: "Delete content", danger: true });
                if (ok) await userAction(user, "purge");
            })
        );
        item.append(head, facts, actions);
        list.append(item);
    }
}

async function updatePremium(user, checkbox) {
    checkbox.disabled = true;
    try {
        await adminRequest(`/admin/users/${encodeURIComponent(user.id)}/premium`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: checkbox.checked })
        });
        user.premium_access = checkbox.checked;
        showAdminMessage(`${user.email}: premium access ${checkbox.checked ? "granted" : "revoked"}.`);
    } catch (error) {
        checkbox.checked = !checkbox.checked;
        showAdminMessage(error.message, true);
    } finally {
        checkbox.disabled = false;
    }
}

async function loadAudit(offset = 0) {
    const list = document.getElementById("audit-list");
    try {
        const { entries, total } = await adminRequest(`/admin/audit?offset=${offset}`);
        document.getElementById("audit-badge").textContent = String(total ?? entries.length);
        if (!offset) list.replaceChildren();
        document.getElementById("audit-more").hidden = offset + entries.length >= (total ?? 0);
        if (!entries.length && !offset) {
            const empty = document.createElement("li");
            empty.textContent = "No admin actions yet.";
            list.append(empty);
        }
        for (const entry of entries) {
            const row = document.createElement("li");
            row.textContent = `${formatWhen(entry.at)} · ${entry.actor_email} → ${entry.action} → ${entry.target_email}${entry.reason ? ` (${entry.reason})` : ""}`;
            list.append(row);
        }
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

let searchTimer = null;
function reloadUsersFromStart() {
    usersPage = 1;
    loadUsers();
}

function formatSize(bytes) {
    return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function mediaRow(name, sizeText, button) {
    const item = document.createElement("li");
    item.className = "ad-row";
    const label = document.createElement("span");
    label.className = "ad-row-name";
    label.textContent = name;
    item.append(label, button);
    if (sizeText) {
        const size = document.createElement("span");
        size.className = "ad-row-size";
        size.textContent = sizeText;
        item.insertBefore(size, button);
    }
    return item;
}

function emptyRow(text) {
    const empty = document.createElement("li");
    empty.className = "ad-row-empty";
    empty.textContent = text;
    return empty;
}

function deleteButton(path, filename, reload) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-sm btn-danger-outline";
    button.textContent = "Delete";
    button.setAttribute("aria-label", `Delete ${filename}`);
    button.addEventListener("click", async () => {
        button.disabled = true;
        const ok = await window.noorConfirm(`Delete ${filename}?`, "Yeh wapas nahi ho sakta.", { label: "Delete", danger: true });
        if (!ok) {
            button.disabled = false;
            return;
        }
        try {
            await adminRequest(`${path}/${encodeURIComponent(filename)}`, { method: "DELETE" });
            showAdminMessage(`${filename} deleted.`);
            await reload();
        } catch (error) {
            showAdminMessage(error.message, true);
            button.disabled = false;
        }
    });
    return button;
}

async function loadBackgrounds() {
    const list = document.getElementById("background-list");
    const countEl = document.getElementById("stat-backgrounds");
    try {
        const clips = await adminRequest("/admin/backgrounds");
        if (countEl) {
            countEl.dataset.count = String(clips.length);
            countEl.textContent = String(clips.length);
        }
        document.getElementById("stat-backgrounds-badge").textContent = `${clips.length} files`;
        list.replaceChildren();
        if (!clips.length) list.append(emptyRow("No background clips uploaded."));
        for (const clip of clips) {
            list.append(mediaRow(clip.filename, formatSize(clip.size), deleteButton("/admin/backgrounds", clip.filename, loadBackgrounds)));
        }
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

async function loadMusic() {
    const list = document.getElementById("music-list");
    try {
        const result = await adminRequest("/admin/music");
        document.getElementById("music-badge").textContent = `${result.music_files.length} files`;
        list.replaceChildren();
        if (!result.music_files.length) list.append(emptyRow("No background music uploaded."));
        for (const filename of result.music_files) {
            list.append(mediaRow(filename, "", deleteButton("/admin/music", filename, loadMusic)));
        }
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

async function loadDiagnostics() {
    const status = document.getElementById("diagnostics-status");
    const grid = document.getElementById("diagnostics-grid");
    status.textContent = "Loading diagnostics...";
    try {
        const diagnostics = await adminRequest("/admin/diagnostics");
        const megabytes = (bytes) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        const values = [
            ["FFmpeg", diagnostics.ffmpeg_version],
            ["RAQM", diagnostics.raqm ? "True" : "False"],
            ["Quran data", diagnostics.quran_json_present ? `Present · ${diagnostics.quran_ayat_count} ayat` : "Missing"],
            ["Background files", diagnostics.background_file_count],
            ["Music files", diagnostics.music_file_count],
            ["Output disk free", megabytes(diagnostics.output_free_bytes)],
            ["Output folder used", megabytes(diagnostics.output_used_bytes)],
            ["History entries", diagnostics.history_entry_count],
            ["Supabase URL", diagnostics.supabase_url_set ? "Set" : "Not set"],
            ["Supabase anon key", diagnostics.supabase_anon_key_set ? "Set" : "Not set"],
            ["Supabase service key", diagnostics.supabase_service_role_key_set ? "Set" : "Not set"],
            ["Video URL secret", diagnostics.video_url_secret_set ? "Set" : "Not set"]
        ];
        grid.replaceChildren();
        for (const [label, value] of values) {
            const item = document.createElement("div");
            const name = document.createElement("dt");
            name.textContent = label;
            const detail = document.createElement("dd");
            detail.textContent = String(value);
            item.append(name, detail);
            grid.append(item);
        }
        status.textContent = "Diagnostics updated.";
    } catch (error) {
        status.textContent = error.message || "Could not load diagnostics.";
        showAdminMessage(status.textContent, true);
    }
}

// --- uploads: file name/size, progress, inline error ---
const UPLOAD_LIMIT = 50 * 1024 * 1024;

async function sendWithProgress(path, file, contentType, onProgress) {
    const { data: { session } } = await window.supabaseClient.auth.getSession();
    if (!session) throw new Error("Your session expired. Please sign in again.");
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE_URL}${path}`);
        xhr.setRequestHeader("Authorization", `Bearer ${session.access_token}`);
        xhr.setRequestHeader("Content-Type", contentType);
        xhr.upload.onprogress = (event) => event.lengthComputable && onProgress(Math.round((event.loaded / event.total) * 100));
        xhr.onerror = () => reject(new Error("Network error. Upload failed."));
        xhr.onload = () => {
            let body = {};
            try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error body */ }
            if (xhr.status >= 200 && xhr.status < 300) resolve(body);
            else reject(new Error(body.detail || `Upload failed (HTTP ${xhr.status}).`));
        };
        xhr.send(file);
    });
}

function setupUpload({ prefix, endpoint, fallbackType, successText, buttonHtml, reload, maxLabel }) {
    const form = document.getElementById(`${prefix}-upload-form`);
    const input = document.getElementById(`${prefix}-file`);
    const info = document.getElementById(`${prefix}-file-info`);
    const bar = document.getElementById(`${prefix}-progress`);
    const error = document.getElementById(`${prefix}-error`);
    const button = document.getElementById(`${prefix}-upload-button`);
    const fail = (message) => { error.textContent = message; };

    input.addEventListener("change", () => {
        const file = input.files[0];
        fail("");
        bar.hidden = true;
        info.textContent = file ? `${file.name} · ${formatSize(file.size)}` : "No file chosen";
        if (file && file.size > UPLOAD_LIMIT) fail(`${maxLabel} must be 50 MB or smaller.`);
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const file = input.files[0];
        if (!file) return fail("Pehle file chunein.");
        if (file.size > UPLOAD_LIMIT) return fail(`${maxLabel} must be 50 MB or smaller.`);
        fail("");
        button.disabled = true;
        button.textContent = "Uploading…";
        bar.value = 0;
        bar.hidden = false;
        try {
            await sendWithProgress(`${endpoint}?filename=${encodeURIComponent(file.name)}`, file, file.type || fallbackType, (percent) => {
                bar.value = percent;
                info.textContent = `${file.name} · ${formatSize(file.size)} · ${percent}%`;
            });
            input.value = "";
            info.textContent = "No file chosen";
            bar.hidden = true;
            showAdminMessage(successText);
            await reload();
        } catch (uploadError) {
            fail(uploadError.message);
        } finally {
            button.disabled = false;
            button.innerHTML = buttonHtml;
        }
    });
}

// --- accordion sections, remembered per browser ---
const SECTION_KEY = "noorAdminSections";

function initializeSections() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(SECTION_KEY)) || {}; } catch { /* storage blocked */ }
    document.querySelectorAll("details[data-sec]").forEach((details) => {
        const id = details.dataset.sec;
        details.open = id in saved ? Boolean(saved[id]) : id === "users";
        details.addEventListener("toggle", () => {
            saved[id] = details.open;
            try { localStorage.setItem(SECTION_KEY, JSON.stringify(saved)); } catch { /* storage blocked */ }
        });
    });
    document.querySelectorAll(".ad-jump a").forEach((link) => {
        link.addEventListener("click", (event) => {
            event.preventDefault();
            const section = document.querySelector(link.getAttribute("href"));
            section.querySelector("details").open = true; // open first so the scroll lands on the final layout
            section.scrollIntoView({ block: "start" });
            history.replaceState(null, "", link.getAttribute("href"));
        });
    });
}

initializeSections();
setupUpload({
    prefix: "background", endpoint: "/admin/backgrounds", fallbackType: "video/mp4", successText: "Background clip uploaded.",
    buttonHtml: '<i class="fa-solid fa-upload" aria-hidden="true"></i>Upload clip', reload: loadBackgrounds, maxLabel: "Background clip"
});
setupUpload({
    prefix: "music", endpoint: "/admin/music", fallbackType: "audio/mpeg", successText: "Background music uploaded.",
    buttonHtml: '<i class="fa-solid fa-upload" aria-hidden="true"></i>Upload music', reload: loadMusic, maxLabel: "Music file"
});

document.getElementById("refresh-users").addEventListener("click", () => loadUsers());
document.getElementById("refresh-audit").addEventListener("click", () => loadAudit());
document.getElementById("user-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(reloadUsersFromStart, 300);
});
document.querySelectorAll(".ad-chips button").forEach((chip) => {
    chip.addEventListener("click", () => {
        document.getElementById("user-status").value = chip.dataset.status;
        document.querySelectorAll(".ad-chips button").forEach((other) => other.setAttribute("aria-pressed", String(other === chip)));
        reloadUsersFromStart();
    });
});
document.getElementById("users-more").addEventListener("click", () => { usersPage += 1; loadUsers(usersPage); });
document.getElementById("audit-more").addEventListener("click", () => loadAudit(document.getElementById("audit-list").children.length));
document.getElementById("refresh-diagnostics").addEventListener("click", loadDiagnostics);
document.addEventListener("DOMContentLoaded", initializeAdmin);

// ---------------- Picks manager ----------------
const picksUi = { bucket: 30, mode: "urdu", cap: 35, all: [], preview: null, poll: null };

function picksDialog(title, build) {
    return new Promise((resolve) => {
        const dialog = document.createElement("dialog");
        dialog.className = "confirm";
        const heading = document.createElement("h3");
        heading.textContent = title;
        const row = document.createElement("div");
        row.className = "pf-row";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "btn btn-outline";
        cancel.textContent = "Cancel";
        const ok = document.createElement("button");
        ok.type = "button";
        ok.className = "btn btn-primary";
        row.append(cancel, ok);
        const done = (value) => { dialog.close(); dialog.remove(); resolve(value); };
        const { nodes, okText, danger, value } = build();
        ok.textContent = okText;
        if (danger) ok.className = "btn btn-danger";
        dialog.append(heading, ...nodes, row);
        cancel.addEventListener("click", () => done(null));
        ok.addEventListener("click", () => done(value()));
        dialog.addEventListener("cancel", (event) => { event.preventDefault(); done(null); });
        document.body.append(dialog);
        dialog.showModal();
    });
}

function picksConfirm(title, text, okText, danger = false) {
    return picksDialog(title, () => {
        const body = document.createElement("p");
        body.textContent = text;
        return { nodes: [body], okText, danger, value: () => true };
    }).then(Boolean);
}

function picksAsk(title, label, current) {
    return picksDialog(title, () => {
        const field = document.createElement("label");
        field.textContent = label;
        field.htmlFor = "ap-edit-ref";
        const input = document.createElement("input");
        input.id = "ap-edit-ref";
        input.className = "control";
        input.value = current;
        setTimeout(() => input.focus(), 0);
        return { nodes: [field, input], okText: "Save", value: () => input.value.trim() };
    });
}

function picksChips(container, options, current, onPick) {
    container.replaceChildren();
    options.forEach(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.setAttribute("aria-pressed", String(value === current));
        button.addEventListener("click", () => onPick(value));
        container.append(button);
    });
}

async function loadPicks() {
    try {
        const all = await adminRequest("/picks");
        picksUi.cap = all.cap;
        picksUi.all = all.picks;
        document.getElementById("picks-badge").textContent = String(all.picks.length);
        renderPicks();
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

function renderPicks() {
    picksChips(document.getElementById("ap-buckets"), [[30, "30 sec"], [60, "1 min"], [120, "2 min"]], picksUi.bucket,
        (value) => { picksUi.bucket = value; picksUi.preview = null; renderPicks(); });
    picksChips(document.getElementById("ap-modes"), [["urdu", "Urdu-only"], ["arabic", "Arabic-only"]], picksUi.mode,
        (value) => { picksUi.mode = value; picksUi.preview = null; renderPicks(); });
    const rows = picksUi.all.filter((row) => row.bucket === picksUi.bucket && row.mode === picksUi.mode);
    const overBucket = rows.filter((row) => row.over_bucket).length;
    const overCap = rows.filter((row) => !row.selectable).length;
    document.getElementById("ap-counts").textContent =
        `${rows.length} picks · ${overBucket} over the ${picksUi.bucket} s bucket · ${overCap} over the ${picksUi.cap} s server cap (kept, users cannot tick them)`;
    const list = document.getElementById("ap-list");
    list.replaceChildren();
    if (!rows.length) list.append(emptyRow("No picks in this list."));
    rows.forEach((row) => list.append(picksRow(row)));
    document.getElementById("ap-apply").disabled = !picksUi.preview?.add?.length;
}

function picksRow(row) {
    const item = document.createElement("li");
    item.className = "ap-row";
    const top = document.createElement("div");
    top.className = "ap-row-top";
    const ref = document.createElement("strong");
    ref.textContent = row.ref;
    const name = document.createElement("span");
    name.textContent = row.surah_name;
    const secs = document.createElement("span");
    secs.textContent = row.seconds == null ? "? s" : `${row.seconds.toFixed(1)} s`;
    top.append(ref, name, secs);
    [[row.over_bucket, `Over ${row.bucket} s`, "bad"], [!row.selectable, `Over ${picksUi.cap} s cap`, "bad"], [row.trimmed_from, "Trimmed copy", ""]]
        .filter(([show]) => show).forEach(([, text, tone]) => {
            const badge = document.createElement("span");
            badge.className = `pf-badge ${tone}`;
            badge.textContent = text;
            top.append(badge);
        });
    const actions = document.createElement("div");
    actions.className = "ap-row-actions";
    const edit = picksButton("Edit", "btn btn-outline btn-sm", async () => {
        const value = await picksAsk("Edit pick", "Surah:Ayah or range", row.ref);
        if (value && value !== row.ref) await picksCall(`/picks/${encodeURIComponent(row.id)}`, "PUT", { ref: value }, "Pick updated.");
    });
    const trim = picksButton("Trim", "btn btn-outline btn-sm", async () => {
        let preview = null;
        try {
            preview = await adminRequest(`/picks/${encodeURIComponent(row.id)}/trim`, { method: "POST", body: JSON.stringify({}) });
        } catch (error) {
            showAdminMessage(error.message, true);
            return;
        }
        const yes = await picksConfirm("Trim to whole ayat?",
            `${row.ref} (${row.seconds.toFixed(1)} s) becomes a NEW pick ${preview.ref} (${preview.seconds.toFixed(1)} s). The original stays.`, "Create trimmed pick");
        if (yes) await picksCall(`/picks/${encodeURIComponent(row.id)}/trim`, "POST", { confirm: true }, "Trimmed pick created.");
    });
    trim.disabled = !row.over_bucket || !row.ref.includes("-");
    const remove = picksButton("Remove", "btn btn-danger-outline btn-sm", async () => {
        if (await picksConfirm("Remove pick?", `${row.ref} will disappear from the ${row.bucket} s ${row.mode} list.`, "Remove", true)) {
            await picksCall(`/picks/${encodeURIComponent(row.id)}`, "DELETE", null, "Pick removed.");
        }
    });
    actions.append(edit, trim, remove);
    item.append(top, actions);
    return item;
}

function picksButton(text, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.addEventListener("click", async () => {
        button.disabled = true;
        try { await onClick(); } finally { button.disabled = false; }
    });
    return button;
}

async function picksCall(path, method, body, okText) {
    try {
        await adminRequest(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
        showAdminMessage(okText);
        await Promise.all([loadPicks(), loadAudit()]);
    } catch (error) {
        showAdminMessage(error.message, true); // includes Supabase save failures
    }
}

let picksEstimateTimer = null;
function picksEstimate() {
    clearTimeout(picksEstimateTimer);
    const out = document.getElementById("ap-estimate");
    const match = /^(\d{1,3}):(\d{1,3})(?:-(\d{1,3}))?$/.exec(document.getElementById("ap-ref").value.trim());
    if (!match) { out.textContent = ""; return; }
    picksEstimateTimer = setTimeout(async () => {
        const [, surah, start, end] = match;
        const mode = picksUi.mode === "arabic" ? "arabic_only" : "urdu_only";
        try {
            const result = await adminRequest(`/estimate?surah=${surah}&start=${start}&end=${end || start}&mode=${mode}&length_limit=30`);
            const secs = result.range_seconds;
            out.textContent = `Estimate: ${secs.toFixed(1)} s` +
                (secs > picksUi.bucket ? ` — over the ${picksUi.bucket} s bucket` : "") +
                (secs > picksUi.cap ? ` and over the ${picksUi.cap} s server cap` : "");
        } catch (error) {
            out.textContent = error.message;
        }
    }, 400);
}

async function picksImport(apply) {
    const text = document.getElementById("ap-import").value;
    try {
        const result = await adminRequest("/picks/import", {
            method: "POST", body: JSON.stringify({ text, bucket: picksUi.bucket, mode: picksUi.mode, apply })
        });
        picksUi.preview = apply ? null : result;
        const list = document.getElementById("ap-import-result");
        list.replaceChildren();
        const lines = [`${result.add.length} to add`, `${result.duplicates.length} already in the list`, `${result.invalid.length} invalid line(s)`, ...result.invalid];
        lines.forEach((line) => { const li = document.createElement("li"); li.textContent = line; list.append(li); });
        if (apply) {
            showAdminMessage(`Imported ${result.add.length} picks.`);
            await Promise.all([loadPicks(), loadAudit()]);
        } else {
            document.getElementById("ap-apply").disabled = !result.add.length;
        }
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

async function picksExport() {
    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        const response = await fetch(`${API_BASE_URL}/picks/export?bucket=${picksUi.bucket}`, { headers: { Authorization: `Bearer ${session.access_token}` } });
        if (!response.ok) throw new Error("Export failed.");
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = `picks-${picksUi.bucket}s.txt`;
        link.click();
        URL.revokeObjectURL(url);
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

async function picksRecompute(remeasure) {
    const bar = document.getElementById("ap-progress");
    const status = document.getElementById("ap-recompute-status");
    try {
        await adminRequest("/picks/recompute", { method: "POST", body: JSON.stringify({ remeasure }) });
    } catch (error) {
        showAdminMessage(error.message, true);
        return;
    }
    bar.hidden = false;
    clearInterval(picksUi.poll);
    picksUi.poll = setInterval(async () => {
        try {
            const state = await adminRequest("/picks/recompute");
            bar.value = state.total ? Math.round((state.done / state.total) * 100) : 0;
            status.textContent = `${state.state}: ${state.done} / ${state.total} ayat` +
                (state.errors.length ? ` · ${state.errors.length} error(s): ${state.errors.slice(0, 3).join("; ")}` : "") +
                (state.error ? ` · ${state.error}` : "");
            if (state.state !== "running") {
                clearInterval(picksUi.poll);
                bar.hidden = true;
                showAdminMessage(state.state === "done" ? "Durations recomputed." : `Recompute failed: ${state.error}`, state.state !== "done");
                await loadPicks();
            }
        } catch (error) {
            clearInterval(picksUi.poll);
            showAdminMessage(error.message, true);
        }
    }, 1500);
}

document.getElementById("ap-add").addEventListener("submit", async (event) => {
    event.preventDefault();
    const ref = document.getElementById("ap-ref").value.trim();
    await picksCall("/picks", "POST", { bucket: picksUi.bucket, mode: picksUi.mode, ref }, `Added ${ref}.`);
    document.getElementById("ap-ref").value = "";
    document.getElementById("ap-estimate").textContent = "";
});
document.getElementById("ap-ref").addEventListener("input", picksEstimate);
document.getElementById("ap-preview").addEventListener("click", () => picksImport(false));
document.getElementById("ap-apply").addEventListener("click", () => picksImport(true));
document.getElementById("ap-export").addEventListener("click", picksExport);
document.getElementById("ap-recompute").addEventListener("click", () => picksRecompute(false));
document.getElementById("ap-remeasure").addEventListener("click", async () => {
    if (await picksConfirm("Re-measure all audio?", "Re-downloads every picked ayah (about 300 picks). This can take several minutes.", "Re-measure")) picksRecompute(true);
});
document.addEventListener("DOMContentLoaded", loadPicks);
