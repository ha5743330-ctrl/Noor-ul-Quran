// Profile page. Server (service role) is the source of truth for name/avatar/status; user text uses textContent only.
const $ = (id) => document.getElementById(id);
const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"];
const AVATAR_MAX = 2 * 1024 * 1024;
let me = null;

function say(id, message, isError = true) {
    const node = $(id);
    node.textContent = message;
    node.className = `pf-msg ${message ? (isError ? "err" : "ok") : ""}`;
}

function formatBytes(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(0, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function paintAvatar(url) {
    const box = $("pf-avatar");
    box.replaceChildren();
    if (url) {
        const img = document.createElement("img");
        img.alt = "";
        img.src = url;
        box.append(img);
    } else {
        box.textContent = (me.display_name || "?").charAt(0).toUpperCase();
    }
    $("pf-avatar-remove").hidden = !url;
}

function paintProfile() {
    $("pf-name").value = me.display_name;
    $("pf-email").textContent = me.email;
    const state = $("pf-email-state");
    state.textContent = me.email_confirmed ? "Verified" : "Not verified";
    state.className = `pf-badge ${me.email_confirmed ? "" : "warn"}`;
    const pending = $("pf-email-pending");
    pending.hidden = !me.new_email;
    pending.className = "pf-msg ok";
    pending.textContent = me.new_email ? `Pending: confirmation mail sent to ${me.new_email}. Link kholne tak email wahi rahegi.` : "";
    paintAvatar(me.avatar_url);
}

async function loadUsage() {
    try {
        const [usage, access] = await Promise.all([window.noorApi("/me/usage"), window.noorApi("/access")]);
        $("us-videos").textContent = usage.videos;
        $("us-images").textContent = usage.images;
        $("us-storage").textContent = formatBytes(usage.bytes);
        $("us-left").textContent = access.daily_limit == null ? "No limit" : `${Math.max(0, access.daily_remaining)} / ${access.daily_limit}`;
    } catch {
        /* deactivated or offline: leave dashes */
    }
}

async function leave(target = "/auth.html") {
    await window.supabaseClient.auth.signOut();
    sessionStorage.clear();
    window.location.href = target;
}

// --- name ---
$("pf-name-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("pf-name").value.trim();
    if (name.length < 2 || name.length > 40) return say("pf-name-msg", "Name 2 se 40 characters ka hona chahiye.");
    try {
        const result = await window.noorApi("/me", { method: "PUT", json: { display_name: name } });
        me.display_name = result.display_name;
        paintProfile();
        say("pf-name-msg", "Saved.", false);
    } catch (error) {
        say("pf-name-msg", error.message);
    }
});

// --- avatar ---
$("pf-avatar-pick").addEventListener("click", () => $("pf-avatar-file").click());
$("pf-avatar-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    if (!AVATAR_TYPES.includes(file.type)) return say("pf-avatar-msg", "Photo PNG, JPG ya WebP honi chahiye.");
    if (file.size > AVATAR_MAX) return say("pf-avatar-msg", "Photo 2 MB se chhoti honi chahiye.");
    say("pf-avatar-msg", "Uploading…", false);
    try {
        const result = await window.noorApi("/me/avatar", { method: "POST", body: file, headers: { "Content-Type": file.type } });
        me.avatar_url = result.avatar_url;
        paintAvatar(me.avatar_url);
        say("pf-avatar-msg", "Photo updated.", false);
    } catch (error) {
        say("pf-avatar-msg", error.message);
    }
});
$("pf-avatar-remove").addEventListener("click", async () => {
    try {
        await window.noorApi("/me/avatar", { method: "DELETE" });
        me.avatar_url = null;
        paintAvatar(null);
        say("pf-avatar-msg", "Photo removed.", false);
    } catch (error) {
        say("pf-avatar-msg", error.message);
    }
});

// --- email ---
$("pf-email-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("pf-new-email").value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return say("pf-email-msg", "Sahi email address likhein.");
    if (email.toLowerCase() === me.email.toLowerCase()) return say("pf-email-msg", "Yeh pehle se aap ki email hai.");
    const { error } = await window.supabaseClient.auth.updateUser({ email }, { emailRedirectTo: `${window.location.origin}/profile.html` });
    if (error) return say("pf-email-msg", "Email abhi change nahi ho saki. Thori der baad dobara koshish karein.");
    me.new_email = email;
    paintProfile();
    $("pf-new-email").value = "";
    say("pf-email-msg", `Confirmation mail ${email} par bhej di gayi hai. Link kholne par email badlegi.`, false);
});

// --- password ---
document.querySelectorAll(".pw-toggle").forEach((toggle) => {
    toggle.addEventListener("click", () => {
        const input = toggle.parentElement.querySelector("input");
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
        toggle.firstElementChild.className = `fa-solid ${show ? "fa-eye-slash" : "fa-eye"}`;
    });
});
$("pf-pass-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = $("pf-pass-current").value;
    const next = $("pf-pass-new").value;
    if (!current) return say("pf-pass-msg", "Current password likhein.");
    if (next.length < 8) return say("pf-pass-msg", "Naya password kam az kam 8 characters ka ho.");
    if (next !== $("pf-pass-confirm").value) return say("pf-pass-msg", "Naye passwords match nahi karte.");
    try {
        await window.noorApi("/me/password", { method: "POST", json: { current_password: current, new_password: next } });
        event.target.reset();
        $("pf-signout-others").hidden = false;
        say("pf-pass-msg", "Password change ho gaya.", false);
    } catch (error) {
        say("pf-pass-msg", error.message);
    }
});
$("pf-signout-others").addEventListener("click", async () => {
    const { error } = await window.supabaseClient.auth.signOut({ scope: "others" });
    say("pf-pass-msg", error ? "Dusre devices sign out nahi ho sake." : "Dusre devices se sign out ho gaya.", !error ? false : true);
});

// --- export ---
$("pf-export").addEventListener("click", async () => {
    try {
        const data = await window.noorApi("/me/export");
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = "noor-ul-quran-my-data.json";
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        say("pf-export-msg", "");
    } catch (error) {
        say("pf-export-msg", error.message);
    }
});

// --- danger zone ---
$("pf-deactivate").addEventListener("click", async () => {
    const ok = await window.noorConfirm(
        "Deactivate account?",
        "Aap sign out ho jayenge. Dobara sign in karke Reactivate karne tak aap generate ya content dekh nahi sakenge. Files mehfooz rahengi.",
        { label: "Deactivate", danger: true }
    );
    if (!ok) return;
    try {
        await window.noorApi("/me/deactivate", { method: "POST" });
        await leave();
    } catch (error) {
        say("pf-deactivate-msg", error.message);
    }
});

function syncDeleteButton() {
    $("pf-delete").disabled = $("pf-del-confirm").value !== "DELETE" || !$("pf-del-pass").value;
}
$("pf-del-confirm").addEventListener("input", syncDeleteButton);
$("pf-del-pass").addEventListener("input", syncDeleteButton);
$("pf-delete-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("pf-del-confirm").value !== "DELETE") return say("pf-delete-msg", "Confirm karne ke liye DELETE likhein.");
    const ok = await window.noorConfirm(
        "Delete account permanently?",
        "Yeh wapas nahi ho sakta. Saari files aur aap ka account hamesha ke liye delete ho jayega.",
        { label: "Delete forever", danger: true }
    );
    if (!ok) return;
    $("pf-delete").disabled = true;
    say("pf-delete-msg", "Deleting your files and account… thora intezar karein.", false);
    try {
        const result = await window.noorApi("/me/delete", { method: "POST", json: { password: $("pf-del-pass").value, confirm: "DELETE" } });
        say("pf-delete-msg", `Account deleted (${result.items_removed} items removed). Signing out…`, false);
        setTimeout(() => leave("/"), 1500);
    } catch (error) {
        say("pf-delete-msg", error.message);
        syncDeleteButton();
    }
});

document.addEventListener("DOMContentLoaded", async () => {
    const { data: { session } } = window.supabaseClient
        ? await window.supabaseClient.auth.getSession()
        : { data: { session: null } };
    if (!session) {
        window.location.replace(`/auth.html?next=${encodeURIComponent("/profile.html")}`);
        return;
    }
    try {
        me = await window.noorApi("/me");
    } catch (error) {
        if (error.status === 401) return window.location.replace("/auth.html?next=%2Fprofile.html");
        say("pf-name-msg", error.message);
        return;
    }
    paintProfile();
    loadUsage();
});
