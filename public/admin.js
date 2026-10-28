const adminMessage = document.getElementById("admin-message");
const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
let adminUsers = [];
let loadedVerseKey = "";

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
        document.getElementById("admin-email").textContent = access.email;
        await loadUsers();
        await loadBackgrounds();
        await loadMusic();
        await loadDiagnostics();
    } catch (error) {
        showAdminMessage(error.message, true);
    }
}

function renderSummary() {
    const totalUsers = adminUsers.length;
    const premiumUsers = adminUsers.filter((user) => Boolean(user.premium_access)).length;
    const totalUsersStat = document.getElementById("stat-total-users");
    const premiumUsersStat = document.getElementById("stat-premium-users");
    const backgroundsBadge = document.getElementById("stat-backgrounds-badge");
    const backgroundCount = document.getElementById("stat-backgrounds");

    if (totalUsersStat) totalUsersStat.textContent = String(totalUsers);
    if (premiumUsersStat) premiumUsersStat.textContent = String(premiumUsers);
    if (backgroundCount) backgroundCount.textContent = String(backgroundCount.dataset.count || "0");
    if (backgroundsBadge) backgroundsBadge.textContent = `${backgroundCount?.dataset.count || 0} files`;
}

async function loadUsers() {
    const body = document.getElementById("users-table");
    body.innerHTML = '<tr><td colspan="4" class="px-3 py-8 text-center text-slate-500">Loading users...</td></tr>';
    try {
        adminUsers = await adminRequest("/admin/users");
        renderSummary();
        renderUsers();
    } catch (error) {
        body.innerHTML = "";
        showAdminMessage(error.message, true);
    }
}

function renderUsers() {
    const body = document.getElementById("users-table");
    const query = document.getElementById("user-search").value.trim().toLowerCase();
    const filtered = adminUsers.filter((user) =>
        `${user.display_name || ""} ${user.email || ""} ${user.user_id}`.toLowerCase().includes(query)
    );
    document.getElementById("user-count").textContent = `${filtered.length} of ${adminUsers.length} users`;
    body.replaceChildren();

    if (!filtered.length) {
        body.innerHTML = '<tr><td colspan="4" class="px-3 py-8 text-center text-slate-500">No users found.</td></tr>';
        return;
    }

    for (const user of filtered) {
        const row = document.createElement("tr");
        row.className = "hover:bg-slate-900/70";
        const nameCell = document.createElement("td");
        nameCell.className = "px-3 py-4 font-medium text-slate-200";
        nameCell.textContent = user.display_name || "No display name";
        const emailCell = document.createElement("td");
        emailCell.className = "px-3 py-4 text-slate-400";
        emailCell.textContent = user.email || user.user_id;
        const dateCell = document.createElement("td");
        dateCell.className = "px-3 py-4 text-slate-500";
        dateCell.textContent = user.created_at ? new Date(user.created_at).toLocaleDateString() : "";
        const accessCell = document.createElement("td");
        accessCell.className = "px-3 py-4";
        const label = document.createElement("label");
        label.className = "inline-flex items-center gap-2 text-sm text-slate-300";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = Boolean(user.premium_access);
        checkbox.className = "h-4 w-4 accent-amber-500";
        checkbox.setAttribute("aria-label", `Premium access for ${user.email || user.user_id}`);
        checkbox.addEventListener("change", () => updatePremium(user, checkbox));
        const accessText = document.createElement("span");
        accessText.textContent = "Premium";
        label.append(checkbox, accessText);
        accessCell.append(label);
        row.append(nameCell, emailCell, dateCell, accessCell);
        body.append(row);
    }
}

async function updatePremium(user, checkbox) {
    checkbox.disabled = true;
    try {
        await adminRequest(`/admin/users/${encodeURIComponent(user.user_id)}/premium`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: checkbox.checked })
        });
        user.premium_access = checkbox.checked;
        showAdminMessage(`${user.email || user.user_id}: premium access ${checkbox.checked ? "granted" : "revoked"}.`);
    } catch (error) {
        checkbox.checked = !checkbox.checked;
        showAdminMessage(error.message, true);
    } finally {
        checkbox.disabled = false;
    }
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
            item.append(name, size);
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
            item.className = "py-2";
            item.textContent = filename;
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

function getSelectedVerse() {
    const surah = Number(document.getElementById("verse-surah").value);
    const ayah = Number(document.getElementById("verse-ayah").value);
    if (!Number.isInteger(surah) || surah < 1 || surah > 114 || !Number.isInteger(ayah) || ayah < 1) {
        throw new Error("Enter a valid Surah (1–114) and Ayah number.");
    }
    return { surah, ayah, key: `${surah}:${ayah}` };
}

function updateAudioStatuses(content) {
    for (const language of ["arabic", "urdu"]) {
        const status = document.getElementById(`${language}-audio-status`);
        const uploaded = Boolean(content[`${language}_audio`]);
        status.textContent = uploaded ? "Custom audio uploaded" : "Using default audio";
        status.className = uploaded ? "text-emerald-400" : "text-slate-500";
    }
}

async function loadVerseContent(event) {
    event.preventDefault();
    const button = document.getElementById("verse-load-button");
    try {
        const verse = getSelectedVerse();
        button.disabled = true;
        const content = await adminRequest(`/admin/verses/${verse.surah}/${verse.ayah}`);
        loadedVerseKey = verse.key;
        document.getElementById("verse-urdu-text").value = content.urdu_text || "";
        updateAudioStatuses(content);
        showAdminMessage(`Loaded Surah ${verse.surah}, Ayah ${verse.ayah}.`);
    } catch (error) {
        showAdminMessage(error.message, true);
    } finally {
        button.disabled = false;
    }
}

async function saveVerseContent(event) {
    event.preventDefault();
    const button = document.getElementById("verse-save-button");
    try {
        const verse = getSelectedVerse();
        if (loadedVerseKey !== verse.key) {
            throw new Error("Load this Surah and Ayah before saving, so text is saved to the right verse.");
        }
        button.disabled = true;
        const content = await adminRequest(`/admin/verses/${verse.surah}/${verse.ayah}`, {
            method: "PUT",
            body: JSON.stringify({
                urdu_text: document.getElementById("verse-urdu-text").value
            })
        });
        updateAudioStatuses(content);
        showAdminMessage(`Text saved for Surah ${verse.surah}, Ayah ${verse.ayah}.`);
    } catch (error) {
        showAdminMessage(error.message, true);
    } finally {
        button.disabled = false;
    }
}

async function uploadVerseAudio(event) {
    event.preventDefault();
    const input = document.getElementById("verse-audio-file");
    const button = document.getElementById("verse-audio-upload-button");
    const file = input.files[0];
    if (!file) {
        showAdminMessage("Choose an audio file first.", true);
        return;
    }
    if (file.size > 50 * 1024 * 1024) {
        showAdminMessage("Audio file must be 50 MB or smaller.", true);
        return;
    }

    try {
        const verse = getSelectedVerse();
        const language = document.getElementById("verse-audio-language").value;
        button.disabled = true;
        button.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Uploading';
        const filename = encodeURIComponent(file.name);
        const content = await adminRequest(
            `/admin/verses/${verse.surah}/${verse.ayah}/audio/${language}?filename=${filename}`,
            {
                method: "POST",
                body: file,
                headers: { "Content-Type": file.type || "application/octet-stream" }
            }
        );
        updateAudioStatuses(content);
        input.value = "";
        showAdminMessage(`${language === "arabic" ? "Arabic" : "Urdu"} audio uploaded for Surah ${verse.surah}, Ayah ${verse.ayah}.`);
    } catch (error) {
        showAdminMessage(error.message, true);
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fa-solid fa-upload mr-2"></i>Upload audio';
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
document.getElementById("refresh-diagnostics").addEventListener("click", loadDiagnostics);
document.getElementById("user-search").addEventListener("input", renderUsers);
document.getElementById("background-upload-form").addEventListener("submit", uploadBackground);
document.getElementById("music-upload-form").addEventListener("submit", uploadMusic);
document.getElementById("verse-lookup-form").addEventListener("submit", loadVerseContent);
document.getElementById("verse-content-form").addEventListener("submit", saveVerseContent);
document.getElementById("verse-audio-form").addEventListener("submit", uploadVerseAudio);
document.addEventListener("DOMContentLoaded", initializeAdmin);
