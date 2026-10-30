const adminMessage = document.getElementById("admin-message");
const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
let adminStats = { total: 0, premium: 0 };
let selfId = "";
let usersPage = 1;
let usersTotal = 0;
const USERS_PER_PAGE = 50;

function showAdminMessage(message, isError = false) {
    adminMessage.textContent = message;
    adminMessage.className = isError
        ? "rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300"
        : "rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300";
    adminMessage.classList.remove("hidden");
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

async function loadUsers() {
    const list = document.getElementById("users-list");
    const params = new URLSearchParams({
        q: document.getElementById("user-search").value.trim(),
        status: document.getElementById("user-status").value,
        page: String(usersPage)
    });
    try {
        const result = await adminRequest(`/admin/users?${params}`);
        adminStats = result.stats;
        usersTotal = result.total;
        renderSummary();
        renderUsers(result.users);
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
        const done = (value) => { dialog.close(); dialog.remove(); resolve(value); };
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
    const pages = Math.max(1, Math.ceil(usersTotal / USERS_PER_PAGE));
    document.getElementById("users-page").textContent = `Page ${usersPage} of ${pages}`;
    document.getElementById("users-prev").disabled = usersPage <= 1;
    document.getElementById("users-next").disabled = usersPage >= pages;

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
            actionButton("Delete content", "btn-danger-outline", async () => {
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

async function loadAudit() {
    const list = document.getElementById("audit-list");
    try {
        const { entries } = await adminRequest("/admin/audit");
        list.replaceChildren();
        if (!entries.length) {
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

function deleteButton(path, filename, reload) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "shrink-0 text-xs font-semibold text-rose-400 hover:text-rose-300";
    button.textContent = "Delete";
    button.addEventListener("click", async () => {
        if (!window.confirm(`Delete ${filename}?`)) return;
        button.disabled = true;
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
        document.getElementById("stat-backgrounds-badge")?.replaceChildren(document.createTextNode(`${clips.length} files`));
        list.replaceChildren();
        if (!clips.length) {
            const empty = document.createElement("li");
            empty.className = "py-3 text-slate-500";
            empty.textContent = "No background clips uploaded.";
            list.append(empty);
            return;
        }
        for (const clip of clips) {
            const item = document.createElement("li");
            item.className = "flex items-center justify-between gap-4 py-3";
            const name = document.createElement("span");
            name.textContent = clip.filename;
            const size = document.createElement("span");
            size.className = "shrink-0 text-slate-500";
            size.textContent = `${(clip.size / (1024 * 1024)).toFixed(1)} MB`;
            item.append(name, size, deleteButton("/admin/backgrounds", clip.filename, loadBackgrounds));
            list.append(item);
        }
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

async function loadMusic() {
    const list = document.getElementById("music-list");
    try {
        const result = await adminRequest("/admin/music");
        list.replaceChildren();
        for (const filename of result.music_files) {
            const item = document.createElement("li");
            item.className = "flex items-center justify-between gap-4 py-2";
            const name = document.createElement("span");
            name.textContent = filename;
            item.append(name, deleteButton("/admin/music", filename, loadMusic));
            list.append(item);
        }
        if (!result.music_files.length) {
            const empty = document.createElement("li");
            empty.className = "py-2 text-slate-500";
            empty.textContent = "No background music uploaded.";
            list.append(empty);
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
            name.className = "text-xs text-slate-500";
            name.textContent = label;
            const detail = document.createElement("dd");
            detail.className = "mt-1 break-words text-sm font-medium text-slate-200";
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

async function uploadBackground(event) {
    event.preventDefault();
    const input = document.getElementById("background-file");
    const button = document.getElementById("background-upload-button");
    const file = input.files[0];
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
        showAdminMessage("Background clip must be 50 MB or smaller.", true);
        return;
    }

    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Uploading';
    try {
        const filename = encodeURIComponent(file.name);
        await adminRequest(`/admin/backgrounds?filename=${filename}`, {
            method: "POST",
            body: file,
            headers: { "Content-Type": file.type || "video/mp4" }
        });
        input.value = "";
        showAdminMessage("Background clip uploaded.");
        await loadBackgrounds();
    } catch (error) {
        showAdminMessage(error.message, true);
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fa-solid fa-upload mr-2"></i>Upload clip';
    }
}

async function uploadMusic(event) {
    event.preventDefault();
    const input = document.getElementById("music-file");
    const button = document.getElementById("music-upload-button");
    const file = input.files[0];
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
        showAdminMessage("Music file must be 50 MB or smaller.", true);
        return;
    }

    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Uploading';
    try {
        const filename = encodeURIComponent(file.name);
        await adminRequest(`/admin/music?filename=${filename}`, {
            method: "POST",
            body: file,
            headers: { "Content-Type": file.type || "audio/mpeg" }
        });
        input.value = "";
        showAdminMessage("Background music uploaded.");
        await loadMusic();
    } catch (error) {
        showAdminMessage(error.message, true);
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fa-solid fa-upload mr-2"></i>Upload music';
    }
}

document.getElementById("refresh-users").addEventListener("click", loadUsers);
document.getElementById("refresh-audit").addEventListener("click", loadAudit);
document.getElementById("user-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(reloadUsersFromStart, 300);
});
document.getElementById("user-status").addEventListener("change", reloadUsersFromStart);
document.getElementById("users-prev").addEventListener("click", () => { usersPage -= 1; loadUsers(); });
document.getElementById("users-next").addEventListener("click", () => { usersPage += 1; loadUsers(); });
document.getElementById("refresh-diagnostics").addEventListener("click", loadDiagnostics);
document.getElementById("background-upload-form").addEventListener("submit", uploadBackground);
document.getElementById("music-upload-form").addEventListener("submit", uploadMusic);
document.addEventListener("DOMContentLoaded", initializeAdmin);
