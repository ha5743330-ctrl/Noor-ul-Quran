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
    if (!response.ok) {
        const failure = new Error(result?.detail || "Admin request failed.");
        failure.status = response.status;
        throw failure;
    }
    return result;
}

// Skeleton while a list loads; error + Retry (never raw text) when it fails.
function listLoading(list, count = 3) { window.noorUi.skeleton(list, count); }
function listFailed(list, what, error, retry) {
    if (error.status === 401 || error.status === 403) return;
    window.noorUi.error(list, { what, err: error, onRetry: retry });
}
function listEmpty(list, icon, title, text) {
    const li = document.createElement("li");
    list.append(li);
    window.noorUi.empty(li, { icon, title, text });
}

async function initializeAdmin() {
    let apiUp = false;
    const client = window.supabaseClient;
    const main = document.getElementById("main");
    const gate = (show) => { document.querySelector(".ad-shell").classList.add("is-gated"); show(); };
    if (!client) {
        gate(() => window.noorUi.gates.signedOut(main, "Admin console"));
        return;
    }

    const { data: { session } } = await client.auth.getSession();
    if (!session) {
        gate(() => window.noorUi.gates.signedOut(main, "Admin console"));
        return;
    }

    try {
        const access = await adminRequest("/access");
        if (!access.is_admin) {
            gate(() => window.noorUi.gates.notAdmin(main));
            return;
        }
        selfId = access.user_id;
        apiUp = true;
        setApiStatus(true);
        adminRequest("/admin/diagnostics").then(showStateWarning).catch(() => {});
        await loadUsers();
        await loadAudit();
        await Promise.all([loadMedia("clips"), loadMedia("photos"), loadMedia("music")]);
        await loadDiagnostics();
        await loadAnalytics();
        await loadAskUsage();
    } catch (error) {
        if (!apiUp) setApiStatus(false);
        if (error.status === 403) { gate(() => window.noorUi.gates.notAdmin(main)); return; }
        showAdminMessage(window.noorUi.friendly(error), true);
    }
}

// "Dhyan talab": things that need the admin's attention, computed from whatever has loaded so far.
const attn = { failed: 0, storage: null, safe: [], ask: null };

function renderAttention() {
    const items = [];
    if (attn.failed > 0) items.push([`${attn.failed} video${attn.failed > 1 ? "s" : ""} fail hui aaj`, "#/analytics", "Dekhein"]);
    if (attn.storage !== null && attn.storage >= 80) items.push([`Storage ${attn.storage}% bhar chuka`, "#/system", "Status"]);
    if (attn.safe.length) items.push([`State restore nahi hui, changes band hain (${attn.safe.join(", ")})`, "#/system", "Status"]);
    if (attn.ask) items.push([`Ask AI limit ke qareeb: ${attn.ask.today}/${attn.ask.limit}`, "#/askai", "Usage"]);
    const list = document.getElementById("attn-list");
    list.replaceChildren(...(items.length ? items : [["Sab theek hai. Abhi kuch dhyan talab nahi.", "", ""]]).map(([text, href, label]) => {
        const li = document.createElement("li");
        li.append(Object.assign(document.createElement("span"), { textContent: text }));
        if (href) li.append(Object.assign(document.createElement("a"), { href, className: "btn btn-outline btn-sm", textContent: label }));
        return li;
    }));
    document.getElementById("attn-count").textContent = String(items.length);
    document.getElementById("ad-attn").classList.toggle("is-clear", !items.length);
}

// Newest known activity (admin audit or analytics day); the free Supabase plan pauses after 7 idle days.
let lastActivity = 0;
function noteActivity(value) {
    const time = value ? new Date(value).getTime() : NaN;
    if (time > lastActivity) lastActivity = time;
    document.getElementById("sys-activity").textContent =
        `${lastActivity ? `Last activity: ${formatWhen(lastActivity)}. ` : ""}Free Supabase plan pauses the project after 7 days idle; visit the app at least once a week.`;
}

function showStateWarning(diagnostics) {
    const files = Object.entries(diagnostics.state_safe_mode || {});
    attn.safe = files.map(([file]) => file);
    renderAttention();
    const box = document.getElementById("state-warning");
    box.hidden = !files.length;
    box.textContent = files.length
        ? `State not restored; changes disabled (${files.map(([f, s]) => `${f}: HTTP ${s}`).join(", ")}). Retrying every 60 s.`
        : "";
}

function setApiStatus(online) {
    const pill = document.getElementById("ad-pill");
    pill.textContent = online ? "API Online" : "API Offline";
    pill.classList.toggle("bad", !online);
    document.getElementById("ad-status").textContent = online ? "Online" : "Offline";
    document.getElementById("ad-status-note").textContent = online ? "API and auth responding" : "API unreachable";
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
    if (from === 1 && !usersRows.length) listLoading(list, 4);
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
        listFailed(list, "Users", error, () => loadUsers(from));
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
            showAdminMessage(window.noorUi.friendly(error), true);
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
        listEmpty(list, "fa-users", "Koi user nahi mila", "Search ya status filter badal kar dekhein.");
        return;
    }

    const when = (value) => (value ? new Date(value).getTime() || 0 : 0);
    const sorts = {
        new: (a, b) => when(b.created_at) - when(a.created_at),
        signin: (a, b) => when(b.last_sign_in_at) - when(a.last_sign_in_at),
        name: (a, b) => String(a.display_name || a.email).localeCompare(String(b.display_name || b.email)),
        videos: (a, b) => (b.videos_today || 0) - (a.videos_today || 0),
    };
    for (const user of [...users].sort(sorts[document.getElementById("user-sort").value] || sorts.new)) {
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
        showAdminMessage(window.noorUi.friendly(error), true);
    } finally {
        checkbox.disabled = false;
    }
}

async function loadAudit(offset = 0) {
    const list = document.getElementById("audit-list");
    if (!offset) listLoading(list, 3);
    try {
        const { entries, total } = await adminRequest(`/admin/audit?offset=${offset}`);
        document.getElementById("audit-badge").textContent = String(total ?? entries.length);
        if (!offset) { list.replaceChildren(); noteActivity(entries[0]?.at); }
        document.getElementById("audit-more").hidden = offset + entries.length >= (total ?? 0);
        if (!entries.length && !offset) {
            listEmpty(list, "fa-clipboard-list", "Abhi koi admin action nahi", "Jab koi user block ya premium hoga to yahan likha jayega.");
        }
        for (const entry of entries) {
            const row = document.createElement("li");
            row.textContent = `${formatWhen(entry.at)} · ${entry.actor_email} → ${entry.action} → ${entry.target_email}${entry.reason ? ` (${entry.reason})` : ""}`;
            list.append(row);
        }
    } catch (error) {
        if (!offset) list.replaceChildren();
        listFailed(list, "Audit log", error, () => loadAudit(offset));
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
            showAdminMessage(window.noorUi.friendly(error), true);
            button.disabled = false;
        }
    });
    return button;
}

// ---- media: clips, photos and music share one layout (upload card + searchable library) ----
const MEDIA = {
    clips: {
        prefix: "background", list: "background-list", badge: "stat-backgrounds-badge", noun: "clip", icon: "fa-film", title: "Background clips",
        exts: [".mp4"], accept: "video/mp4,.mp4", type: "video/mp4", endpoint: "/admin/backgrounds", ok: "Background clip uploaded.",
        empty: ["Abhi koi background clip nahi", "Left se MP4 clip upload karein."],
        rows: (clips) => clips.map((clip) => ({ name: clip.filename, meta: formatSize(clip.size) })),
        badgeText: (rows) => `${rows.length} files`,
    },
    photos: {
        prefix: "photo", list: "photo-list", badge: "photos-badge", noun: "photo", icon: "fa-image", title: "Photos", multiple: true,
        exts: [".jpg", ".jpeg", ".png", ".webp"], accept: "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp", type: "image/jpeg",
        endpoint: "/admin/photos", ok: "Photos uploaded.", note: "cover-cropped to 1080x1920, compressed to 400 KB. Disabled photos stay stored but are not used.",
        empty: ["Abhi koi photo nahi", "Ayah Images built-in background istemal kar rahi hain. Left se photo upload karein."],
        rows: (photos) => photos.map((photo) => ({
            name: photo.filename, meta: `${formatSize(photo.size)}${photo.enabled ? "" : " · off"}`, off: !photo.enabled, enabled: photo.enabled,
            thumb: new URL(photo.thumb_url, new URL(API_BASE_URL, window.location.origin).origin).href,
        })),
        badgeText: (rows) => `${rows.filter((row) => row.enabled).length} of ${rows.length} on`,
    },
    music: {
        prefix: "music", list: "music-list", badge: "music-badge", noun: "track", icon: "fa-music", title: "Background music",
        exts: [".mp3", ".mpeg", ".wav", ".aac", ".m4a"], accept: ".mp3,.mpeg,.wav,.aac,.m4a,audio/mpeg,audio/mp4,audio/aac,audio/wav", type: "audio/mpeg",
        endpoint: "/admin/music", ok: "Background music uploaded.",
        empty: ["Abhi koi music nahi", "Left se audio file upload karein."],
        rows: (result) => result.music_files.map((name) => ({ name, meta: "Audio" })),
        badgeText: (rows) => `${rows.length} files`,
    },
};
const mediaRows = { clips: [], photos: [], music: [] };

function buildMediaView(key, c) {
    const p = c.prefix;
    const many = c.multiple ? "s" : "";
    document.getElementById(`${key}-section`).innerHTML = `
        <div class="ad-split">
            <form id="${p}-upload-form" class="ad-card ad-upload" novalidate>
                <h2>Upload ${c.noun}${many}</h2>
                <input id="${p}-file" class="ad-file-input" type="file" accept="${c.accept}"${c.multiple ? " multiple" : ""}>
                <label for="${p}-file" id="${p}-drop" class="ad-drop"><i class="fa-solid ${c.icon}" aria-hidden="true"></i><strong>Drop ${c.noun}${many} here</strong><span>or <u>choose ${c.multiple ? "files" : "a file"}</u></span></label>
                ${c.multiple ? "" : `<label class="ad-field" for="${p}-name">Name (optional)</label><input id="${p}-name" class="control" maxlength="80" autocomplete="off" placeholder="e.g. mountain-valley">`}
                <p class="ad-limits">${c.exts.join(" ")} · max 50 MB${c.multiple ? " each" : ""}${c.note ? ` · ${c.note}` : ""}</p>
                <p id="${p}-file-info" class="ad-file-info" aria-live="polite">No file chosen</p>
                <progress id="${p}-progress" max="100" value="0" hidden aria-label="Upload progress"></progress>
                <p id="${p}-error" class="pf-msg err" role="alert"></p>
                <button id="${p}-upload-button" type="submit" class="btn btn-primary"><i class="fa-solid fa-upload" aria-hidden="true"></i>Upload ${c.noun}${many}</button>
            </form>
            <section class="ad-card ad-lib">
                <div class="ad-count-row"><h2>Library <span id="${c.badge}" class="pf-badge">0</span></h2></div>
                <input id="${p}-search" type="search" class="control" placeholder="Search by name" aria-label="Search ${c.noun}s">
                <ul id="${c.list}" class="ad-media-grid"></ul>
            </section>
        </div>`;
    document.getElementById(`${p}-search`).addEventListener("input", () => renderMedia(key));
}

function renderMedia(key) {
    const c = MEDIA[key];
    const list = document.getElementById(c.list);
    const query = document.getElementById(`${c.prefix}-search`).value.trim().toLowerCase();
    const rows = mediaRows[key].filter((row) => row.name.toLowerCase().includes(query));
    list.replaceChildren();
    if (!mediaRows[key].length) return listEmpty(list, c.icon, c.empty[0], c.empty[1]);
    if (!rows.length) return listEmpty(list, "fa-magnifying-glass", "Koi match nahi", "Doosra naam try karein.");
    for (const row of rows) {
        const item = document.createElement("li");
        item.className = `ad-media${row.off ? " is-off" : ""}`;
        const thumb = document.createElement("span");
        thumb.className = "ad-thumb";
        if (row.thumb) {
            const img = document.createElement("img");
            img.loading = "lazy";
            img.alt = "";
            img.src = row.thumb;
            thumb.append(img);
        } else {
            thumb.innerHTML = `<i class="fa-solid ${c.icon}" aria-hidden="true"></i>`;
        }
        const info = document.createElement("span");
        info.className = "ad-media-info";
        const name = document.createElement("strong");
        name.textContent = row.name;
        const meta = document.createElement("small");
        meta.textContent = row.meta;
        info.append(name, meta);
        const actions = document.createElement("span");
        actions.className = "ad-media-actions";
        if (key === "photos") actions.append(actionButton(row.enabled ? "Disable" : "Enable", "btn-outline", async () => {
            await adminRequest(`/admin/photos/${encodeURIComponent(row.name)}`, { method: "PATCH", body: JSON.stringify({ enabled: !row.enabled }) });
            await loadMedia("photos");
        }));
        actions.append(deleteButton(c.endpoint, row.name, () => loadMedia(key)));
        item.append(thumb, info, actions);
        list.append(item);
    }
}

async function loadMedia(key) {
    const c = MEDIA[key];
    const list = document.getElementById(c.list);
    if (!mediaRows[key].length) listLoading(list, 3);
    try {
        mediaRows[key] = c.rows(await adminRequest(c.endpoint));
        document.getElementById(c.badge).textContent = c.badgeText(mediaRows[key]);
        if (key === "clips") {
            const count = document.getElementById("stat-backgrounds");
            count.dataset.count = count.textContent = String(mediaRows[key].length);
        }
        renderMedia(key);
    } catch (error) {
        list.replaceChildren();
        listFailed(list, c.title, error, () => loadMedia(key));
    }
}

function drawChart(chart, rows) {
    const max = Math.max(1, ...rows.map((d) => d.views));
    const step = 600 / rows.length;
    const ns = "http://www.w3.org/2000/svg";
    chart.replaceChildren(...rows.flatMap((d, i) => [["views", "var(--chart-1)"], ["uniques", "var(--chart-2)"]].map(([key, fill], j) => {
        const h = Math.max(1, (d[key] / max) * 130);
        const bar = document.createElementNS(ns, "rect");
        const w = step * 0.4;
        bar.setAttribute("x", i * step + step * 0.1 + j * w);
        bar.setAttribute("y", 140 - h);
        bar.setAttribute("width", w);
        bar.setAttribute("height", h);
        bar.style.fill = fill;
        const title = document.createElementNS(ns, "title");
        title.textContent = `${d.day} ${key}: ${d[key]}`;
        bar.append(title);
        return bar;
    })));
    chart.removeAttribute("hidden"); // SVG elements have no .hidden property
}

async function loadDiagnostics() {
    const status = document.getElementById("diagnostics-status");
    const grid = document.getElementById("diagnostics-grid");
    status.textContent = "Loading diagnostics...";
    try {
        const diagnostics = await adminRequest("/admin/diagnostics");
        showStateWarning(diagnostics);
        const megabytes = (bytes) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        const flag = (on) => ({ pill: on ? "Set" : "Not set", bad: !on });
        const values = [
            ["FFmpeg", diagnostics.ffmpeg_version],
            ["RAQM", diagnostics.raqm ? "True" : "False"],
            ["Quran data", diagnostics.quran_json_present ? `Present · ${diagnostics.quran_ayat_count} ayat` : "Missing"],
            ["Background files", diagnostics.background_file_count],
            ["Music files", diagnostics.music_file_count],
            ["Output disk free", megabytes(diagnostics.output_free_bytes)],
            ["Output folder used", megabytes(diagnostics.output_used_bytes)],
            ["History entries", diagnostics.history_entry_count],
            ["Supabase URL", flag(diagnostics.supabase_url_set)],
            ["Supabase anon key", flag(diagnostics.supabase_anon_key_set)],
            ["Supabase service key", flag(diagnostics.supabase_service_role_key_set)],
            ["Video URL secret", flag(diagnostics.video_url_secret_set)]
        ];
        grid.replaceChildren();
        for (const [label, value] of values) {
            const item = document.createElement("div");
            const name = document.createElement("dt");
            name.textContent = label;
            const detail = document.createElement("dd");
            if (value && value.pill) {
                const badge = document.createElement("span");
                badge.className = `pf-badge${value.bad ? " bad" : ""}`;
                badge.textContent = value.pill;
                detail.append(badge);
            } else {
                detail.textContent = String(value);
            }
            item.append(name, detail);
            grid.append(item);
        }
        const supabaseOk = diagnostics.supabase_url_set && diagnostics.supabase_service_role_key_set;
        const pill = document.getElementById("sys-supabase");
        pill.textContent = supabaseOk ? "Supabase OK" : "Supabase incomplete";
        pill.classList.toggle("bad", !supabaseOk);
        const total = diagnostics.output_free_bytes + diagnostics.output_used_bytes;
        attn.storage = total > 0 ? Math.round((diagnostics.output_used_bytes / total) * 100) : null;
        renderAttention();
        document.getElementById("stat-disk").textContent = megabytes(diagnostics.output_free_bytes);
        document.getElementById("stat-disk-note").textContent = `free · Supabase ${supabaseOk ? "configured" : "incomplete"}`;
        status.textContent = "Diagnostics updated.";
    } catch (error) {
        status.textContent = error.message || "Could not load diagnostics.";
        showAdminMessage(status.textContent, true);
    }
}

async function loadAskUsage() {
    try {
        const u = await adminRequest("/admin/ask-usage");
        const today = (u.days.find((d) => d.day === new Date().toISOString().slice(0, 10)) || { n: 0 }).n;
        document.getElementById("stat-ask").textContent = `${today}/${u.global_limit}`;
        document.getElementById("ask-usage").textContent = `Ask AI is ${u.enabled ? "on" : "OFF"}. Today ${today} of ${u.global_limit} questions used.`;
        document.getElementById("ask-cards").replaceChildren(
            userFact("Today", `${today} / ${u.global_limit}`), userFact("Per-user limit", String(u.user_limit)),
            userFact("API key", u.key_set ? "Set" : "MISSING"), userFact("Last error", u.last_error || "none"));
        document.getElementById("ask-days").replaceChildren(...u.days.slice(0, 7).map((d) => {
            const li = document.createElement("li");
            li.textContent = `${d.day}: ${d.n} questions`;
            return li;
        }));
        attn.ask = u.global_limit && today / u.global_limit >= 0.8 ? { today, limit: u.global_limit } : null;
        renderAttention();
    } catch (error) {
        document.getElementById("ask-usage").textContent = "Ask AI usage unavailable.";
    }
}

async function loadAnalytics() {
    const status = document.getElementById("analytics-status");
    const table = document.getElementById("analytics-table");
    try {
        const { days, status: st, failed_videos: failed = [] } = await adminRequest("/admin/analytics?days=30");
        document.getElementById("dash-failed").hidden = !failed.length;
        document.getElementById("dash-failed-list").replaceChildren(...failed.map((f) => {
            const li = document.createElement("li");
            li.textContent = `${formatWhen(f.at)} · ${f.reason || "reason not saved"}`;
            return li;
        }));
        document.getElementById("analytics-flush").textContent = st
            ? `Last flush: ${st.last_flush || "never"} · buffered views: ${st.buffered_views} · last error: ${st.last_error || "none"}${st.enabled ? "" : " · analytics disabled (no service key)"}`
            : "";
        const latest = days[0];
        if (latest) {
            const set = (id, v) => { document.getElementById(id).textContent = v; };
            set("stat-videos", latest.videos); set("stat-failures", latest.failures);
            set("stat-render", latest.avg_render_secs ? `${latest.avg_render_secs}s` : "-");
            set("stat-views", `${latest.views} / ${latest.uniques}`);
            attn.failed = latest.day === new Date().toISOString().slice(0, 10) ? latest.failures : 0;
            noteActivity([...days].find((d) => d.views > 0)?.day);
        }
        renderAttention();
        if (!days.length) {
            status.dataset.kind = "empty";
            status.textContent = "No analytics yet (or the analytics table is not set up).";
            return;
        }
        status.hidden = table.hidden = false;
        status.hidden = true;
        document.getElementById("dash-chart-empty").hidden = true;
        drawChart(document.getElementById("dash-chart"), [...days].slice(0, 14).reverse());
        drawChart(document.getElementById("analytics-chart"), [...days].reverse());
        table.tBodies[0].replaceChildren(...days.map((d) => {
            const tr = document.createElement("tr");
            const labels = ["Day", "Views", "Visitors", "Signups", "Videos", "Failed", "Avg render"];
            for (const [i, v] of [d.day, d.views, d.uniques, d.signups, d.videos, d.failures, d.avg_render_secs ? `${d.avg_render_secs}s` : "-"].entries()) {
                const td = document.createElement("td");
                td.dataset.label = labels[i];
                td.textContent = v;
                tr.append(td);
            }
            return tr;
        }));
    } catch (error) {
        status.dataset.kind = "error";
        status.textContent = "Analytics unavailable.";
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

function setupUpload(key, c) {
    const $ = (part) => document.getElementById(`${c.prefix}-${part}`);
    const [form, input, info, bar, error, button, drop, nameInput] = ["upload-form", "file", "file-info", "progress", "error", "upload-button", "drop", "name"].map($);
    const buttonHtml = button.innerHTML;
    const fail = (message) => { error.textContent = message; };
    const problem = (file) => {
        const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
        if (!c.exts.includes(ext)) return `${file.name}: only ${c.exts.join(" ")} files are allowed.`;
        if (!file.size) return `${file.name}: the file is empty.`;
        if (file.size > UPLOAD_LIMIT) return `${file.name}: ${formatSize(file.size)} is over the 50 MB limit.`;
        return "";
    };
    const refresh = () => {
        const files = [...input.files];
        bar.hidden = true;
        fail(files.map(problem).filter(Boolean).join(" "));
        info.textContent = !files.length ? "No file chosen"
            : files.length === 1 ? `${files[0].name} · ${formatSize(files[0].size)}`
            : `${files.length} files · ${formatSize(files.reduce((sum, file) => sum + file.size, 0))}`;
    };
    input.addEventListener("change", refresh);
    for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("is-over"); });
    for (const type of ["dragleave", "drop"]) drop.addEventListener(type, () => drop.classList.remove("is-over"));
    drop.addEventListener("drop", (event) => {
        event.preventDefault();
        if (event.dataTransfer?.files.length) { input.files = event.dataTransfer.files; refresh(); }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const files = [...input.files];
        if (!files.length) return fail("Pehle file chunein.");
        const valid = files.filter((file) => !problem(file));
        const failed = files.filter(problem).map(problem);
        if (!valid.length) return fail(failed.join(" "));
        button.disabled = true;
        button.textContent = "Uploading…";
        bar.value = 0;
        bar.hidden = false;
        let uploaded = 0;
        for (const [index, file] of valid.entries()) {
            let filename = file.name;
            const custom = valid.length === 1 && nameInput?.value.trim();
            if (custom) filename = /\.[^.]+$/.test(custom) ? custom : custom + (file.name.match(/\.[^.]+$/) || [""])[0];
            try {
                await sendWithProgress(`${c.endpoint}?filename=${encodeURIComponent(filename)}`, file, file.type || c.type, (percent) => {
                    bar.value = Math.round(((index + percent / 100) / valid.length) * 100);
                    info.textContent = `${filename} · ${formatSize(file.size)} · ${percent}%`;
                });
                uploaded += 1;
            } catch (uploadError) {
                failed.push(`${file.name}: ${uploadError.message}`);
            }
        }
        input.value = "";
        if (nameInput) nameInput.value = "";
        info.textContent = "No file chosen";
        bar.hidden = true;
        button.disabled = false;
        button.innerHTML = buttonHtml;
        fail(failed.join(" "));
        if (uploaded) {
            showAdminMessage(c.ok);
            window.noorUi.toast("Upload ho gaya");
            await loadMedia(key);
        }
    });
}

// --- hash routing (#/users, #/clips, ...) + sidebar drawer ---
const VIEWS = {
    dashboard: ["Dashboard", "Aaj ki halat aur jo cheezein dhyan maangti hain."],
    users: ["Users", "Premium, block, sign out everywhere, ya content delete karein."],
    clips: ["Video clips", "Reels ke background videos. Upload karein, dekhein, hatayein."],
    photos: ["Photos", "Ayah Images aur photo-background videos ke liye."],
    music: ["Music", "Background music for generated videos."],
    picks: ["Picks", "Curated ayah lists users generate from."],
    analytics: ["Analytics", "Views, visitors, videos aur failures."],
    askai: ["Ask AI usage", "Aaj ke sawal aur limits."],
    system: ["Settings and status", "Keys, Supabase aur server ki sehat."],
};

function setDrawer(open) {
    document.body.classList.toggle("ad-nav-open", open);
    document.getElementById("ad-burger").setAttribute("aria-expanded", String(open));
    document.getElementById("ad-backdrop").hidden = !open;
}

function route() {
    let view = (location.hash.match(/^#\/([\w-]+)/) || [])[1];
    if (!VIEWS[view]) view = "dashboard";
    document.querySelectorAll(".ad-view").forEach((node) => { node.hidden = node.dataset.view !== view; });
    document.querySelectorAll(".ad-link").forEach((link) => {
        if (link.dataset.nav === view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    document.getElementById("ad-title").textContent = VIEWS[view][0];
    document.getElementById("ad-sub").textContent = VIEWS[view][1];
    document.title = `${VIEWS[view][0]} | Noor ul Quran Admin`;
    setDrawer(false);
    window.scrollTo(0, 0);
}

for (const [key, c] of Object.entries(MEDIA)) {
    buildMediaView(key, c);
    setupUpload(key, c);
}
window.addEventListener("hashchange", route);
document.getElementById("ad-burger").addEventListener("click", () => setDrawer(!document.body.classList.contains("ad-nav-open")));
document.getElementById("ad-backdrop").addEventListener("click", () => setDrawer(false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && document.body.classList.contains("ad-nav-open")) { setDrawer(false); document.getElementById("ad-burger").focus(); } });
route();
noteActivity();
renderAttention();

document.getElementById("refresh-users").addEventListener("click", () => loadUsers());
document.getElementById("refresh-audit").addEventListener("click", () => loadAudit());
document.getElementById("user-sort").addEventListener("change", () => renderUsers(usersRows));
document.getElementById("user-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(reloadUsersFromStart, 300);
});
document.querySelectorAll("#users-section .ad-chips button").forEach((chip) => {
    chip.addEventListener("click", () => {
        document.getElementById("user-status").value = chip.dataset.status;
        document.querySelectorAll("#users-section .ad-chips button").forEach((other) => other.setAttribute("aria-pressed", String(other === chip)));
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
    if (!picksUi.all.length) listLoading(document.getElementById("ap-list"), 3);
    try {
        const all = await adminRequest("/picks");
        picksUi.cap = all.cap;
        picksUi.all = all.picks;
        document.getElementById("picks-badge").textContent = String(all.picks.length);
        renderPicks();
    } catch (error) {
        const list = document.getElementById("ap-list");
        list.replaceChildren();
        listFailed(list, "Picks", error, loadPicks);
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
    if (!rows.length) listEmpty(list, "fa-list-check", "Is list mein abhi koi pick nahi", "Upar ref likh kar (jaise 112:1-4) pehla pick add karein.");
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
            showAdminMessage(window.noorUi.friendly(error), true);
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
        showAdminMessage(window.noorUi.friendly(error), true); // includes Supabase save failures
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
        showAdminMessage(window.noorUi.friendly(error), true);
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
        showAdminMessage(window.noorUi.friendly(error), true);
    }
}

async function picksRecompute(remeasure) {
    const bar = document.getElementById("ap-progress");
    const status = document.getElementById("ap-recompute-status");
    try {
        await adminRequest("/picks/recompute", { method: "POST", body: JSON.stringify({ remeasure }) });
    } catch (error) {
        showAdminMessage(window.noorUi.friendly(error), true);
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
            showAdminMessage(window.noorUi.friendly(error), true);
        }
    }, 1500);
}

document.getElementById("ap-add").addEventListener("submit", async (event) => {
    event.preventDefault();
    const refInput = document.getElementById("ap-ref");
    const ref = refInput.value.trim();
    if (!/^\d{1,3}:\d{1,3}(-\d{1,3})?$/.test(ref)) {
        window.noorUi.fieldError(refInput, "Format likhein Surah:Start-End, jaise 112:1-4.");
        return;
    }
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
