const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
window.userAccess = null;
window.videoStyles = {};
let generatedVideos = [];
let currentVideoIndex = 0;
let latestBatchJobId = null;
let estimateTimer = null;
let estimateRequestId = 0;
let lastEstimateRangeSeconds = 0;
const RECENT_LIMIT = 5;
let recentItems = [];
let recentSelecting = false;
const recentSelected = new Set();
const DIRECT_DOWNLOAD_LIMIT = 5;
// Quran text always uses the verified KFGQPC font; only its color is adjustable.
const QURAN_STYLE_ELEMENTS = new Set(["bismillah", "arabic"]);

const VIDEO_STYLE_LABELS = {
    bismillah: "Bismillah",
    surah: "Surah Name",
    label: "Ayat Range",
    arabic: "Arabic Ayaat",
    urdu: "Urdu Tarjuma",
    english: "English Translation",
    channel: "Channel Name"
};
const VIDEO_STYLE_FONT_LABELS = {
    amiri_regular: "Amiri Regular",
    amiri_bold: "Amiri Bold",
    naskh_regular: "Naskh Regular",
    naskh_bold: "Naskh Bold",
    nastaliq_regular: "Nastaliq Regular",
    nastaliq_bold: "Nastaliq Bold"
};

document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("reel-generator-form")) {
        initializeVideoStyleControls();
        initializeDurationEstimate();
        initializeStudio();
        initializeReciterChoices();
        initializeRecentSelection();
        document.getElementById("generate-image-button")?.addEventListener("click", generateAyahImage);
        initializeImageSuggestions();
    } else {
        initializeLandingAuth();
    }
});

async function generateAyahImage(event) {
    event.preventDefault();
    const button = document.getElementById("generate-image-button");
    const status = document.getElementById("image-generation-status");
    const reference = document.getElementById("image-ayah")?.value.trim() || "";
    if (button?.disabled) return;
    if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    }
    if (status) {
        status.textContent = "Creating your image...";
        status.classList.remove("hidden");
    }
    try {
        const { data: { session }, error } = await window.supabaseClient.auth.getSession();
        if (error) throw error;
        if (!session) {
            redirectToAuth();
            return;
        }
        const response = await fetch(`${API_BASE_URL}/generate-image`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: "Bearer " + session.access_token
            },
            body: JSON.stringify(ayahToSend(reference))
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || `Image generation failed (HTTP ${response.status}).`);

        const image = document.getElementById("generated-ayah-image");
        const download = document.getElementById("download-image-btn");
        const resultBox = document.getElementById("image-result-box");
        const resultReference = document.getElementById("image-result-reference");
        if (!image || !download || !resultBox || !result.image_data) {
            throw new Error("The generated image response was incomplete.");
        }
        image.src = result.image_data;
        download.href = result.image_data;
        download.download = result.filename || "noor-ul-quran-ayah.png";
        if (resultReference) resultReference.textContent = result.reference || "";
        resultBox.classList.remove("hidden");
        document.getElementById("empty-results")?.classList.add("hidden");
        if (status) status.textContent = `Image ready: ${result.reference}`;
        resultBox.scrollIntoView({ behavior: "smooth", block: "center" });
        await refreshRecentVideos();
    } catch (error) {
        console.error("Ayah image generation error:", error);
        if (status) status.textContent = error.message || "Could not generate the ayah image.";
    } finally {
        if (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
        }
    }
}

function redirectToAuth() {
    const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.location.replace(`/auth.html?next=${encodeURIComponent(returnTo)}`);
}

async function initializeLandingAuth() {
    const client = window.supabaseClient;
    if (!client) return;
    try {
        const { data: { session }, error } = await client.auth.getSession();
        if (error) throw error;
        const statusTag = document.getElementById("user-status-tag");
        const authButton = document.getElementById("nav-auth-btn");
        const adminLink = document.getElementById("admin-portal-link");
        if (!session) {
            if (statusTag) statusTag.classList.add("hidden");
            return;
        }
        if (authButton) {
            authButton.textContent = "Sign Out";
            authButton.href = "#";
            authButton.title = session.user.email || "Sign Out";
            authButton.onclick = handleSignOut;
        }

        const response = await fetch(`${API_BASE_URL}/access`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        if (!response.ok) throw new Error(`Could not load account access (HTTP ${response.status}).`);
        window.userAccess = await response.json();
        if (statusTag) {
            statusTag.textContent = window.userAccess.is_admin
                ? "Admin" : window.userAccess.premium_access ? "Premium" : "Free Tier";
            statusTag.classList.remove("hidden");
        }
        if (adminLink) adminLink.classList.toggle("hidden", !window.userAccess.is_admin);
    } catch (error) {
        console.error("Landing account initialization failed:", error);
    }
}

function applyFreeTierLimits() {
    const access = window.userAccess || {};
    const cap = access.max_video_seconds;
    const select = document.getElementById("input-max-dur");
    if (select && cap && ![...select.options].some((option) => Number(option.value) === cap)) {
        // The server cap is the longest free length; offer exactly that instead of a hard-coded value.
        const capOption = new Option(`${cap} seconds`, String(cap));
        const next = [...select.options].find((option) => Number(option.value) > cap);
        select.add(capOption, next || null);
    }
    document.querySelectorAll("#input-max-dur option").forEach((option) => {
        option.disabled = Boolean(cap && Number(option.value) > cap);
    });
    if (select && cap && select.selectedOptions[0]?.disabled) select.value = String(cap);
    const remaining = document.getElementById("daily-remaining");
    if (remaining) {
        remaining.classList.remove("hidden");
        if (access.is_admin) {
            remaining.textContent = "Admin: no daily limit";
        } else if (access.daily_limit == null || access.daily_remaining == null) {
            console.error("Daily limit fields missing from /access response:", access);
            remaining.textContent = "Daily limit: checking...";
        } else {
            const left = Math.max(0, access.daily_remaining);
            remaining.textContent = left > 0
                ? `${left} of ${access.daily_limit} videos left today`
                : "Daily limit reached, try tomorrow";
        }
    }
    const generateButton = document.getElementById("generate-button");
    if (generateButton && limitReached()) generateButton.disabled = true;
}

function limitReached() {
    const access = window.userAccess || {};
    return access.daily_limit != null && (access.daily_remaining ?? 0) <= 0;
}

function formatWait(seconds) {
    const minutes = Math.round((Number(seconds) || 0) / 60);
    return minutes < 1 ? "under a minute" : `about ${minutes} min`;
}

function formatDuration(seconds) {
    const rounded = Math.max(0, Math.round(Number(seconds) || 0));
    return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}

function currentEstimateRange() {
    return {
        surah: Number(document.getElementById("estimate-surah")?.value),
        start: Number(document.getElementById("estimate-start")?.value),
        end: Number(document.getElementById("estimate-end")?.value)
    };
}

function syncRangeText() {
    const { surah, start, end } = currentEstimateRange();
    const rangeInput = document.getElementById("input-pick");
    if (rangeInput && surah && start && end) rangeInput.value = `${surah}:${start}-${end}`;
}

function syncRangeControls() {
    const rangeInput = document.getElementById("input-pick");
    const match = rangeInput?.value.trim().match(/^(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?$/);
    if (!match) return false;
    const surah = document.getElementById("estimate-surah");
    const start = document.getElementById("estimate-start");
    const end = document.getElementById("estimate-end");
    if (surah) surah.value = match[1];
    if (start) start.value = match[2];
    if (end) end.value = match[3] || match[2];
    return true;
}

function scheduleDurationEstimate() {
    window.clearTimeout(estimateTimer);
    const mode = document.getElementById("mode-select")?.value || "pick";
    const panel = document.getElementById("duration-estimate-panel");
    if (panel) panel.classList.toggle("hidden", mode !== "pick");
    if (mode !== "pick") return;
    estimateTimer = window.setTimeout(requestDurationEstimate, 400);
}

function updateMusicAvailability() {
    const arabicOnly = document.getElementById("input-content-mode")?.value === "arabic_only";
    const musicSelect = document.getElementById("input-music");
    const note = document.getElementById("arabic-only-audio-note");
    if (musicSelect) musicSelect.disabled = arabicOnly;
    if (note) note.classList.toggle("hidden", !arabicOnly);
    document.getElementById("urdu-voice-control")?.classList.toggle("hidden", arabicOnly);
}

async function initializeReciterChoices() {
    const arabicSelect = document.getElementById("input-reciter");
    const urduSelect = document.getElementById("input-urdu-voice");
    if (!arabicSelect || !urduSelect) return;
    const status = document.getElementById("reciter-load-status");
    try {
        const response = await fetch(`${API_BASE_URL}/reciters`);
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !Array.isArray(result.reciters) || !result.defaults) {
            throw new Error(result.detail || "Reciter choices could not be loaded.");
        }
        for (const [select, language, defaultId] of [
            [arabicSelect, "ar", result.defaults.ar],
            [urduSelect, "ur", result.defaults.ur]
        ]) {
            const choices = result.reciters.filter((item) => item.language === language);
            if (!choices.some((item) => item.id === defaultId)) {
                throw new Error(`Default ${language} reciter is missing from the list.`);
            }
            select.replaceChildren(...choices.map((item) => {
                const option = document.createElement("option");
                option.value = item.id;
                option.textContent = item.display_name;
                return option;
            }));
            select.value = defaultId;
        }
        status?.classList.add("hidden");
        [arabicSelect, urduSelect].forEach((select) => {
            select.addEventListener("change", scheduleDurationEstimate);
        });
    } catch (error) {
        if (status) {
            status.textContent = error.message || "Reciter choices could not be loaded.";
            status.classList.remove("hidden");
        }
    }
}

async function requestDurationEstimate() {
    const text = document.getElementById("duration-estimate-text");
    const warning = document.getElementById("duration-estimate-warning");
    const chooseButton = document.getElementById("choose-estimated-length");
    const { surah, start, end } = currentEstimateRange();
    const length = Number(document.getElementById("input-max-dur")?.value || 60);
    const contentMode = document.getElementById("input-content-mode")?.value || "full";
    const currentRequestId = ++estimateRequestId;

    if (!Number.isInteger(surah) || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
        if (text) {
            text.textContent = "Surah aur ayat range durust enter karein.";
            text.className = "text-sm font-semibold text-red-400";
        }
        if (warning) warning.classList.add("hidden");
        if (chooseButton) chooseButton.classList.add("hidden");
        return;
    }
    if (text) {
        text.textContent = "Hisaab ho raha hai...";
        text.className = "text-sm font-semibold text-slate-300";
    }
    if (warning) warning.classList.add("hidden");
    if (chooseButton) chooseButton.classList.add("hidden");

    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) throw new Error("Sign in to estimate reel duration.");
        const params = new URLSearchParams({
            surah: String(surah),
            start: String(start),
            end: String(end),
            mode: contentMode,
            length_limit: String(length)
        });
        const reciterId = document.getElementById("input-reciter")?.value;
        const urduVoiceId = document.getElementById("input-urdu-voice")?.value;
        if (reciterId) params.set("reciter_id", reciterId);
        if (urduVoiceId && contentMode !== "arabic_only") params.set("urdu_voice_id", urduVoiceId);
        const response = await fetch(`${API_BASE_URL}/estimate?${params}`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Duration estimate failed.");
        if (currentRequestId !== estimateRequestId) return;

        lastEstimateRangeSeconds = Number(result.range_seconds || result.estimated_seconds);
        const allFit = result.fits_up_to >= end && result.estimated_seconds <= length;
        const withinTolerance = result.within_tolerance;
        if (text) {
            text.textContent = `Anumaan: ${formatDuration(result.estimated_seconds)} (ayat ${start}-${result.fits_up_to}) | Selected: ${formatDuration(length)}`;
            text.className = `text-sm font-semibold ${allFit ? "text-green-400" : withinTolerance ? "text-amber-300" : "text-red-400"}`;
        }
        if (warning && result.fits_up_to < end) {
            warning.textContent = `Sirf ayat ${start}-${result.fits_up_to} fit hongi. Length badhayein ya range chhoti karein.`;
            warning.classList.remove("hidden");
        }
        if (warning && result.fits_up_to >= end && result.estimated_seconds > length) {
            warning.textContent = `Ayat ${start} ki poori audio selected length se lambi hai; poori ayat render hogi.`;
            warning.classList.remove("hidden");
        }
        if (chooseButton && (result.fits_up_to < end || result.estimated_seconds > length)) {
            chooseButton.classList.remove("hidden");
        }
    } catch (error) {
        if (currentRequestId !== estimateRequestId) return;
        if (text) {
            text.textContent = error.message || "Duration estimate failed.";
            text.className = "text-sm font-semibold text-red-400";
        }
        if (warning) warning.classList.add("hidden");
    }
}

function initializeDurationEstimate() {
    const surah = document.getElementById("estimate-surah");
    if (surah) {
        for (let number = 2; number <= 114; number += 1) {
            const option = document.createElement("option");
            option.value = String(number);
            option.textContent = `Surah ${number}`;
            surah.appendChild(option);
        }
    }
    surah?.addEventListener("change", () => {
        syncRangeText();
        scheduleDurationEstimate();
    });
    ["estimate-start", "estimate-end"].forEach((id) => {
        document.getElementById(id)?.addEventListener("input", () => {
            syncRangeText();
            scheduleDurationEstimate();
        });
    });
    document.getElementById("input-pick")?.addEventListener("input", () => {
        if (syncRangeControls()) scheduleDurationEstimate();
    });
    document.getElementById("input-max-dur")?.addEventListener("change", scheduleDurationEstimate);
    document.getElementById("input-content-mode")?.addEventListener("change", () => {
        updateMusicAvailability();
        scheduleDurationEstimate();
    });
    updateMusicAvailability();
    document.getElementById("choose-estimated-length")?.addEventListener("click", () => {
        const lengthSelect = document.getElementById("input-max-dur");
        if (!lengthSelect) return;
        const available = [...lengthSelect.options]
            .filter((option) => !option.disabled)
            .map((option) => Number(option.value))
            .sort((a, b) => a - b);
        const recommended = available.find((value) => value >= lastEstimateRangeSeconds);
        lengthSelect.value = String(recommended || available[available.length - 1]);
        scheduleDurationEstimate();
    });
    syncRangeText();
    scheduleDurationEstimate();
}

function renderAppliedVideoStyles() {
    const list = document.getElementById("applied-video-styles");
    if (!list) return;
    list.replaceChildren();
    Object.entries(window.videoStyles).forEach(([element, style]) => {
        const item = document.createElement("li");
        const fontLabel = style.font ? VIDEO_STYLE_FONT_LABELS[style.font] : "KFGQPC Quran font";
        item.textContent = `${VIDEO_STYLE_LABELS[element]}: ${style.color} · ${fontLabel}`;
        list.appendChild(item);
    });
    list.classList.toggle("hidden", Object.keys(window.videoStyles).length === 0);
}

function renderGenerationWarnings(warnings) {
    const list = document.getElementById("generation-warnings");
    if (!list) return;
    const uniqueWarnings = [...new Set(Array.isArray(warnings) ? warnings : [])];
    list.replaceChildren(...uniqueWarnings.map((warning) => {
        const item = document.createElement("li");
        item.textContent = warning;
        return item;
    }));
    list.classList.toggle("hidden", uniqueWarnings.length === 0);
}

function syncStyleFontControl() {
    const element = document.getElementById("video-style-element")?.value;
    const fontSelect = document.getElementById("video-style-font");
    if (fontSelect) fontSelect.disabled = QURAN_STYLE_ELEMENTS.has(element);
}

function initializeVideoStyleControls() {
    document.getElementById("video-style-element")?.addEventListener("change", syncStyleFontControl);
    syncStyleFontControl();
    document.getElementById("apply-video-style")?.addEventListener("click", () => {
        const element = document.getElementById("video-style-element")?.value;
        const color = document.getElementById("video-style-color")?.value;
        const font = document.getElementById("video-style-font")?.value;
        if (!element || !color || !font) return;
        window.videoStyles[element] = QURAN_STYLE_ELEMENTS.has(element) ? { color } : { color, font };
        renderAppliedVideoStyles();
    });
    document.getElementById("reset-video-styles")?.addEventListener("click", () => {
        window.videoStyles = {};
        renderAppliedVideoStyles();
    });
}

// Background Music List Load Karne Ka Function
async function loadMusicList() {
    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) {
            console.warn("No Supabase session found for loading music list.");
            return;
        }
        const response = await fetch(`${API_BASE_URL}/music-list`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        if (!response.ok) {
            console.error("Failed to fetch music list:", response.status, response.statusText);
            return;
        }
        const data = await response.json();
        console.log("Loaded music files from server:", data);
        
        const select = document.getElementById("input-music");
        if (!select) {
            console.warn("Element #input-music not found in DOM!");
            return;
        }
        
        select.innerHTML = '<option value="">Koi music nahi (No background music)</option>';
        if (data.music_files && data.music_files.length > 0) {
            data.music_files.forEach(file => {
                const opt = document.createElement("option");
                opt.value = file;
                opt.textContent = file;
                select.appendChild(opt);
            });
            console.log("Music dropdown populated with:", data.music_files);
        } else {
            console.log("No music files found in backgrounds/music/");
        }
    } catch (e) {
        console.error("Could not load music list error:", e);
    }
}

async function initializeStudio() {
    const client = window.supabaseClient;
    if (!client) {
        redirectToAuth();
        return;
    }

    const { data: { session }, error } = await client.auth.getSession();
    if (error || !session) {
        redirectToAuth();
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/access`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        if (!response.ok) {
            if (response.status === 401) {
                await client.auth.signOut();
                redirectToAuth();
                return;
            }
            const result = await response.json();
            throw new Error(result.detail || "Could not load account access.");
        }
        window.userAccess = await response.json();
    } catch (error) {
        console.error("Could not load account access:", error);
        const remaining = document.getElementById("daily-remaining");
        if (remaining) remaining.textContent = "Daily limit: unavailable";
        const accessMessage = document.getElementById("access-message");
        if (accessMessage) {
            accessMessage.textContent = error.message || "Could not load account access.";
            accessMessage.classList.remove("hidden");
        }
        const generateButton = document.getElementById("generate-button");
        if (generateButton) generateButton.disabled = true;
        return;
    }

    const statusTag = document.getElementById("user-status-tag");
    const authButton = document.getElementById("nav-auth-btn");
    const adminLink = document.getElementById("admin-portal-link");
    if (statusTag) statusTag.textContent = window.userAccess.is_admin
        ? "Admin" : window.userAccess.premium_access ? "Premium" : "Free Tier";
    if (authButton) {
        authButton.textContent = "Sign Out";
        authButton.href = "#";
        authButton.title = session.user.email || "Sign Out";
        authButton.onclick = handleSignOut;
    }
    if (adminLink) adminLink.classList.toggle("hidden", !window.userAccess.is_admin);
    const hasPremium = Boolean(window.userAccess.is_admin || window.userAccess.premium_access);
    document.querySelectorAll("#input-max-dur option").forEach((option) => {
        option.disabled = !hasPremium && Number(option.value) > 60;
    });
    setMode("pick");
    applyFreeTierLimits();
    
    // Yahan loadMusicList ko call kar diya gaya hai taake dropdown mein music load ho jaye
    await loadMusicList();
    await refreshRecentVideos();
}

// Video Generation Function with Preview Link & Music Parameter
async function generateVideo(event) {
    if (event) event.preventDefault();

    const mode = document.getElementById("mode-select")?.value || "pick";
    const contentMode = document.getElementById("input-content-mode")?.value || "full";
    const verse = document.getElementById("input-pick")?.value || "";
    const duration = document.getElementById("input-max-dur")?.value || 60;

    const statusBox = document.getElementById("status-box");
    const outputBox = document.getElementById("output-box");
    const logMsg = document.getElementById("log-message");
    const statusPercent = document.getElementById("status-percent");
    const statusHeading = document.getElementById("status-heading");
    const statusSpinner = document.getElementById("status-spinner");
    const generateButton = document.getElementById("generate-button");

    if (mode === "pick" && !verse) {
        alert("Please enter a Surah:Verse range (e.g., 55:1-8)");
        return;
    }
    if (generateButton?.disabled) return;
    // Picks mode sends server pick ids (never refs); picks.js owns that selection.
    const picksBatch = mode === "picks" ? window.NoorPicks?.batchRequest() : null;
    if (mode === "picks" && !picksBatch) return;
    latestBatchJobId = null;
    document.getElementById("download-all-btn")?.classList.add("hidden");

    if (outputBox) outputBox.classList.remove("hidden");
    if (logMsg) logMsg.innerText = "> Sending request to Python FFmpeg Rendering Engine...";
    if (statusPercent) statusPercent.innerText = "Working";
    if (generateButton) {
        generateButton.disabled = true;
        generateButton.setAttribute("aria-busy", "true");
    }

    if (statusBox) {
        statusBox.innerText = "⏳ Request sent! Rendering started in background...";
        statusBox.style.color = "#d97706";
    }
    if (statusHeading) statusHeading.textContent = "Status: rendering";
    if (statusSpinner) statusSpinner.className = "fa-solid fa-spinner animate-spin text-xs text-amber-300";
    renderGenerationWarnings([]);

    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) {
            redirectToAuth();
            return;
        }
        const response = await fetch(`${API_BASE_URL}/generate`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${session.access_token}`
            },
            body: JSON.stringify({
                mode: mode,
                verse: verse,
                max_duration: parseInt(duration, 10),
                random_count: parseInt(document.getElementById("input-random-count")?.value || "1", 10),
                quality: document.getElementById("input-quality")?.value || "fast",
                content_mode: contentMode,
                reciter_id: document.getElementById("input-reciter")?.value || null,
                urdu_voice_id: contentMode === "arabic_only"
                    ? null
                    : document.getElementById("input-urdu-voice")?.value || null,
                styles: window.videoStyles,
                music: contentMode === "arabic_only"
                    ? null
                    : document.getElementById("input-music")?.value || null,
                ...picksBatch
            })
        });

        let result = await response.json().catch(() => ({ detail: `Server returned HTTP ${response.status}.` }));
        const generationJobId = result.job_id;
        if (response.status === 202 && result.job_id) {
            window.NoorPicks?.onJob(result);
            result = await waitForGeneration(result.job_id, session.access_token, statusBox, logMsg, statusPercent);
        }

        if (response.ok && result.status !== "failed") {
            if (logMsg) logMsg.innerText = "✓ " + (result.message || "Video rendering completed.");
            if (statusPercent) statusPercent.innerText = "100%";
            if (statusHeading) statusHeading.textContent = result.status === "partial"
                ? "Status: completed with warnings" : "Status: complete";
            if (statusSpinner) statusSpinner.className = result.status === "partial"
                ? "fa-solid fa-triangle-exclamation text-xs text-amber-300"
                : "fa-solid fa-circle-check text-xs text-emerald-300";
            if (statusBox) {
                statusBox.innerText = result.status === "partial"
                    ? "⚠ Some reels could not be rendered. Completed reels are available below."
                    : `✅ ${result.videos?.length || 1} reel(s) ready to preview.`;
                statusBox.style.color = result.status === "partial" ? "#d97706" : "#059669";
            }
            const videos = Array.isArray(result.videos) ? result.videos : result.filename ? [result] : [];
            setBatchDownload(result.job_id || generationJobId, videos);
            renderGenerationWarnings(result.warnings || videos.flatMap((video) => video.warnings || []));
            showGeneratedVideos(videos);
            await refreshRecentVideos();
        } else {
            if (result.videos?.length) {
                setBatchDownload(result.job_id || generationJobId, result.videos);
                showGeneratedVideos(result.videos);
            }
            const errorMessage = result.error || result.detail || "Generation failed.";
            if (logMsg) logMsg.innerText = "❌ Error: " + errorMessage;
            if (statusPercent) statusPercent.innerText = "0%";
            if (statusHeading) statusHeading.textContent = "Status: failed";
            if (statusSpinner) statusSpinner.className = "fa-solid fa-circle-exclamation text-xs text-red-300";
            if (statusBox) {
                statusBox.innerText = "❌ Error: " + errorMessage;
                statusBox.style.color = "#dc2626";
            }
        }
    } catch (error) {
        console.error("API Error:", error);
        const errorMessage = error.message || "Unable to connect to FastAPI Backend Server.";
        if (logMsg) logMsg.innerText = "❌ " + errorMessage;
        if (statusPercent) statusPercent.innerText = "0%";
        if (statusHeading) statusHeading.textContent = "Status: failed";
        if (statusSpinner) statusSpinner.className = "fa-solid fa-circle-exclamation text-xs text-red-300";
        if (statusBox) {
            statusBox.innerText = "❌ " + errorMessage;
            statusBox.style.color = "#dc2626";
        }
    } finally {
        if (generateButton) {
            generateButton.removeAttribute("aria-busy");
            const hasPremium = Boolean(window.userAccess?.is_admin || window.userAccess?.premium_access);
            const currentMode = document.getElementById("mode-select")?.value || "pick";
            generateButton.disabled = (currentMode !== "pick" && !hasPremium) || limitReached();
        }
    }
}

async function waitForGeneration(jobId, accessToken, statusBox, logMsg, statusPercent, initial = {}) {
    const showQueue = (job) => {
        if (job.status !== "queued" || job.queue_ahead == null || !statusBox) return;
        statusBox.innerText = `⏳ ${job.queue_ahead} before you · wait ${formatWait(job.estimated_wait_seconds)}`;
        statusBox.style.color = "#d97706";
    };
    showQueue(initial);
    let lastVideoCount = 0;
    let pollDelayMs = 2000;
    let retryStartedAt = null;
    while (true) {
        await new Promise((resolve) => setTimeout(resolve, pollDelayMs));
        const response = await fetch(`${API_BASE_URL}/generate/${encodeURIComponent(jobId)}`, {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        const job = await response.json().catch(() => ({ error: `Status check returned HTTP ${response.status}.` }));
        if (!response.ok) {
            const message = job.detail || job.error || `Status check returned HTTP ${response.status}.`;
            if (![408, 425, 429].includes(response.status) && response.status < 500) {
                throw new Error(message);
            }
            if (retryStartedAt === null) retryStartedAt = Date.now();
            if (Date.now() - retryStartedAt >= 5 * 60 * 1000) {
                throw new Error(`Generation status is still unavailable after 5 minutes: ${message}`);
            }
            const retryInSeconds = Math.ceil(pollDelayMs / 1000);
            const reconnectMessage = `Temporary connection issue while checking generation status. Retrying in ${retryInSeconds}s.`;
            if (statusBox) {
                statusBox.innerText = `⚠ ${reconnectMessage}`;
                statusBox.style.color = "#d97706";
            }
            const statusHeading = document.getElementById("status-heading");
            if (statusHeading) statusHeading.textContent = "Status: reconnecting";
            if (logMsg) logMsg.innerText = `⚠ ${reconnectMessage}`;
            pollDelayMs = Math.min(pollDelayMs * 2, 15000);
            continue;
        }
        retryStartedAt = null;
        pollDelayMs = 2000;

        window.NoorPicks?.onJob(job);
        const videos = Array.isArray(job.videos) ? job.videos : [];
        renderGenerationWarnings(job.warnings || videos.flatMap((video) => video.warnings || []));
        if (videos.length) showGeneratedVideos(videos);
        if (videos.length !== lastVideoCount) {
            lastVideoCount = videos.length;
            if (statusPercent) statusPercent.innerText = `${lastVideoCount} ready`;
            const statusHeading = document.getElementById("status-heading");
            if (statusHeading) statusHeading.textContent = `Status: rendering · ${lastVideoCount} ready`;
            if (logMsg) logMsg.innerText = `✓ ${lastVideoCount} reel(s) ready; remaining reels are still rendering.`;
            if (statusBox) {
                statusBox.innerText = `✅ ${lastVideoCount} reel(s) ready. You can preview or download them while the batch continues.`;
                statusBox.style.color = "#059669";
            }
        }
        if (["completed", "partial", "failed"].includes(job.status)) return job;
    }
}

function showGeneratedVideos(videos) {
    const wasEmpty = generatedVideos.length === 0;
    const selectedFilename = generatedVideos[currentVideoIndex]?.filename;
    const previousUrls = new Map(
        generatedVideos
            .filter((video) => video.filename && video.url)
            .map((video) => [video.filename, video.url])
    );
    const previousCount = generatedVideos.length;
    generatedVideos = videos.map((video) => ({
        ...video,
        url: video.url || previousUrls.get(video.filename)
    }));
    const emptyResults = document.getElementById("empty-results");
    if (emptyResults) emptyResults.classList.toggle("hidden", generatedVideos.length > 0);
    const selectedIndex = generatedVideos.findIndex((video) => video.filename === selectedFilename);
    currentVideoIndex = selectedIndex >= 0 ? selectedIndex : Math.min(currentVideoIndex, generatedVideos.length - 1);
    const previewBox = document.getElementById("video-preview-box");
    if (!generatedVideos.length || !previewBox) {
        if (!generatedVideos.length) {
            const player = document.getElementById("rendered-video-player");
            if (player) {
                player.pause();
                player.removeAttribute("src");
                player.load();
            }
        }
        if (previewBox) previewBox.classList.add("hidden");
        return;
    }
    previewBox.classList.remove("hidden");
    if (previousCount !== generatedVideos.length || selectedIndex < 0) renderCurrentVideo();
    if (wasEmpty) previewBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function resolveVideoUrl(video) {
    if (video.url) {
        const currentUrl = new URL(video.url, window.location.origin);
        const apiPath = new URL(API_BASE_URL, window.location.origin).pathname.replace(/\/$/, "");
        if (!currentUrl.pathname.startsWith(`${apiPath}/video/`)) return video.url;
        const expiry = Number(currentUrl.searchParams.get("exp"));
        if (expiry > Date.now() / 1000 + 30) return video.url;
    }
    if (!video.filename) throw new Error("Generated video filename is missing.");
    const { data: { session } } = await window.supabaseClient.auth.getSession();
    if (!session) throw new Error("Sign in again to play or download this video.");
    const response = await fetch(`${API_BASE_URL}/video-url/${encodeURIComponent(video.filename)}`, {
        headers: { Authorization: `Bearer ${session.access_token}` }
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Could not create a secure video link.");
    return new URL(result.url, new URL(API_BASE_URL, window.location.origin).origin).href;
}

function setRecentVideosStatus(message) {
    const status = document.getElementById("recent-videos-status");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("hidden", !message);
}

function createHistoryBadge(label, tone = "slate") {
    const badge = document.createElement("span");
    const tones = {
        slate: "border-slate-600 bg-slate-800 text-slate-200",
        amber: "border-amber-400/40 bg-amber-400/10 text-amber-200",
        green: "border-emerald-400/40 bg-emerald-400/10 text-emerald-200",
        red: "border-red-400/40 bg-red-400/10 text-red-200"
    };
    badge.className = `inline-flex items-center rounded-full border px-2 py-1 text-[11px] font-semibold ${tones[tone] || tones.slate}`;
    badge.textContent = label;
    return badge;
}

function updateRecentSelectionUi() {
    const actions = document.getElementById("recent-bulk-actions");
    const toggle = document.getElementById("recent-select-toggle");
    const count = document.getElementById("recent-selected-count");
    const selectAll = document.getElementById("recent-select-all");
    actions?.classList.toggle("hidden", !recentSelecting);
    actions?.classList.toggle("flex", recentSelecting);
    toggle?.classList.toggle("hidden", recentSelecting || recentItems.length === 0);
    if (count) count.textContent = `${recentSelected.size} selected`;
    if (selectAll) {
        selectAll.checked = recentItems.length > 0 && recentSelected.size === recentItems.length;
        selectAll.indeterminate = recentSelected.size > 0 && recentSelected.size < recentItems.length;
    }
    const downloadable = recentItems.some((item) => recentSelected.has(item.id) && item.has_file);
    const downloadButton = document.getElementById("recent-download-selected");
    const deleteButton = document.getElementById("recent-delete-selected");
    if (downloadButton) downloadButton.disabled = !downloadable;
    if (deleteButton) deleteButton.disabled = recentSelected.size === 0;
}

function setRecentSelecting(selecting) {
    recentSelecting = selecting;
    recentSelected.clear();
    renderRecentVideos(recentItems);
}

function initializeRecentSelection() {
    document.getElementById("recent-select-toggle")?.addEventListener("click", () => setRecentSelecting(true));
    document.getElementById("recent-select-cancel")?.addEventListener("click", () => setRecentSelecting(false));
    document.getElementById("recent-select-all")?.addEventListener("change", (event) => {
        recentSelected.clear();
        if (event.target.checked) recentItems.forEach((item) => recentSelected.add(item.id));
        renderRecentVideos(recentItems);
    });
    document.getElementById("recent-download-selected")?.addEventListener("click", downloadSelectedRecent);
    document.getElementById("recent-delete-selected")?.addEventListener("click", deleteSelectedRecent);
}

function renderRecentVideos(videos) {
    const list = document.getElementById("recent-videos-list");
    const empty = document.getElementById("recent-videos-empty");
    if (!list || !empty) return;
    recentItems = videos;
    const ids = new Set(videos.map((item) => item.id));
    [...recentSelected].forEach((id) => { if (!ids.has(id)) recentSelected.delete(id); });
    if (!videos.length) recentSelecting = false;
    list.replaceChildren();
    empty.classList.toggle("hidden", videos.length > 0);

    videos.forEach((video) => {
        const isImage = video.type === "image";
        const item = document.createElement("li");
        item.className = "flex gap-3 rounded-xl border border-slate-700 bg-slate-900/80 p-3";

        if (recentSelecting) {
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "mt-1 h-5 w-5 shrink-0";
            checkbox.checked = recentSelected.has(video.id);
            checkbox.setAttribute("aria-label", `Select ${isImage ? "image" : "video"} from ${video.created_at || "history"}`);
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) recentSelected.add(video.id);
                else recentSelected.delete(video.id);
                updateRecentSelectionUi();
            });
            item.appendChild(checkbox);
        }

        if (isImage && video.thumb_url) {
            const thumb = document.createElement("img");
            thumb.src = new URL(video.thumb_url, new URL(API_BASE_URL, window.location.origin).origin).href;
            thumb.alt = `Ayah image ${video.reference || ""}`;
            thumb.loading = "lazy";
            thumb.className = "h-20 w-16 shrink-0 rounded-lg border border-slate-700 object-cover";
            item.appendChild(thumb);
        }

        const body = document.createElement("div");
        body.className = "min-w-0 flex-1";
        const heading = document.createElement("div");
        heading.className = "flex flex-wrap items-start justify-between gap-2";
        const title = document.createElement("p");
        title.className = "text-sm font-semibold text-white";
        if (isImage) {
            title.textContent = `Ayah ${video.reference || ""}`;
        } else {
            const range = video.start_ayah === video.end_ayah
                ? `${video.start_ayah ?? ""}`
                : `${video.start_ayah ?? ""}-${video.end_ayah ?? ""}`;
            title.textContent = `Surah ${video.surah ?? ""} · Ayat ${range}`;
        }
        heading.appendChild(title);

        const badges = document.createElement("div");
        badges.className = "flex flex-wrap gap-1.5";
        badges.appendChild(createHistoryBadge(isImage ? "Image" : "Video", "slate"));
        if (video.content_mode === "urdu_only") badges.appendChild(createHistoryBadge("Urdu-only", "amber"));
        if (video.content_mode === "arabic_only") badges.appendChild(createHistoryBadge("Arabic + English", "amber"));
        if (video.styled) badges.appendChild(createHistoryBadge("Styled", "amber"));
        if (video.downloaded_at || Number(video.download_count) > 0) {
            badges.appendChild(createHistoryBadge("Downloaded", "green"));
        }
        if (!video.has_file) badges.appendChild(createHistoryBadge("Expire", "red"));
        heading.appendChild(badges);
        body.appendChild(heading);

        const createdAt = document.createElement("p");
        createdAt.className = "mt-1 text-xs text-slate-300";
        const date = new Date(video.created_at);
        createdAt.textContent = Number.isNaN(date.getTime())
            ? "Date unavailable" : date.toLocaleString();
        body.appendChild(createdAt);

        if (!recentSelecting) {
            const actions = document.createElement("div");
            actions.className = "mt-3 flex flex-wrap gap-2";
            const playButton = document.createElement("button");
            playButton.type = "button";
            playButton.className = "min-h-9 rounded-lg bg-amber-400 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-50";
            playButton.textContent = isImage ? "Open" : "Play";
            playButton.disabled = !video.has_file;
            playButton.addEventListener("click", () => isImage ? openRecentImage(video) : playRecentVideo(video));

            const downloadButton = document.createElement("button");
            downloadButton.type = "button";
            downloadButton.className = "min-h-9 rounded-lg border border-slate-500 px-3 py-2 text-xs font-semibold text-slate-100 hover:border-amber-300 hover:text-amber-200 disabled:cursor-not-allowed disabled:opacity-50";
            downloadButton.textContent = "Download";
            downloadButton.disabled = !video.has_file;
            downloadButton.addEventListener("click", () => downloadRecentVideo(video));

            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.className = "min-h-9 rounded-lg border border-red-400/50 px-3 py-2 text-xs font-semibold text-red-200 hover:bg-red-400/10";
            deleteButton.textContent = "Delete";
            deleteButton.addEventListener("click", () => deleteRecentVideo(video));

            actions.append(playButton, downloadButton, deleteButton);
            body.appendChild(actions);
        }
        item.appendChild(body);
        list.appendChild(item);
    });
    updateRecentSelectionUi();
}

async function historyRequest(path, body) {
    const { data: { session }, error } = await window.supabaseClient.auth.getSession();
    if (error) throw error;
    if (!session) throw new Error("Sign in again to manage your items.");
    const response = await fetch(`${API_BASE_URL}${path}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${session.access_token}`
        },
        body: JSON.stringify(body)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `Request failed (HTTP ${response.status}).`);
    return result;
}

function triggerDownload(url) {
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
}

async function downloadSelectedRecent() {
    const items = recentItems.filter((item) => recentSelected.has(item.id) && item.has_file);
    if (!items.length) return;
    try {
        if (items.length <= DIRECT_DOWNLOAD_LIMIT) {
            for (const item of items) {
                triggerDownload(await requestRecentVideoUrl(item, "download"));
                await new Promise((resolve) => setTimeout(resolve, 400));
            }
        } else {
            const result = await historyRequest("/history/bulk-download-url", { ids: items.map((item) => item.id) });
            triggerDownload(new URL(result.url, new URL(API_BASE_URL, window.location.origin).origin).href);
        }
        setRecentSelecting(false);
        setRecentVideosStatus("");
        await refreshRecentVideos();
    } catch (error) {
        console.error("Bulk download error:", error);
        setRecentVideosStatus(error.message || "Could not download the selected items.");
    }
}

async function deleteSelectedRecent() {
    const ids = [...recentSelected];
    if (!ids.length) return;
    if (!window.confirm(`Delete ${ids.length} items? This cannot be undone.`)) return;
    try {
        await historyRequest("/history/bulk-delete", { ids });
        const removed = new Set(recentItems.filter((item) => recentSelected.has(item.id)).map((item) => item.filename));
        if (generatedVideos.some((item) => removed.has(item.filename))) {
            showGeneratedVideos(generatedVideos.filter((item) => !removed.has(item.filename)));
        }
        setRecentSelecting(false);
        setRecentVideosStatus("");
        await refreshRecentVideos();
    } catch (error) {
        console.error("Bulk delete error:", error);
        setRecentVideosStatus(error.message || "Could not delete the selected items.");
    }
}

async function openRecentImage(video) {
    if (!video.has_file) return;
    try {
        window.open(await requestRecentVideoUrl(video, "play"), "_blank", "noopener");
    } catch (error) {
        setRecentVideosStatus(error.message || "Could not open this image.");
    }
}

async function refreshRecentVideos() {
    const list = document.getElementById("recent-videos-list");
    if (!list) return;
    try {
        const { data: { session }, error } = await window.supabaseClient.auth.getSession();
        if (error) throw error;
        if (!session) {
            renderRecentVideos([]);
            return;
        }
        const response = await fetch(`${API_BASE_URL}/history`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not load recent videos.");
        renderRecentVideos((Array.isArray(result.videos) ? result.videos : []).slice(0, RECENT_LIMIT)); // full list lives in /library.html
        setRecentVideosStatus("");
    } catch (error) {
        console.error("Recent video history error:", error);
        setRecentVideosStatus(error.message || "Could not load recent videos.");
    }
}

async function requestRecentVideoUrl(video, purpose) {
    const { data: { session }, error } = await window.supabaseClient.auth.getSession();
    if (error) throw error;
    if (!session) throw new Error("Sign in again to access this video.");
    const response = await fetch(
        `${API_BASE_URL}/history/${encodeURIComponent(video.id)}/url?purpose=${purpose}`,
        { headers: { Authorization: `Bearer ${session.access_token}` } }
    );
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Could not open this video.");
    return new URL(result.url, new URL(API_BASE_URL, window.location.origin).origin).href;
}

async function playRecentVideo(video) {
    if (!video.has_file) return;
    try {
        const url = await requestRecentVideoUrl(video, "play");
        showGeneratedVideos([{ ...video, url }]);
        setRecentVideosStatus("");
    } catch (error) {
        console.error("Recent video playback error:", error);
        setRecentVideosStatus(error.message || "Could not play this video.");
    }
}

async function downloadRecentVideo(video) {
    if (!video.has_file) return;
    try {
        const url = await requestRecentVideoUrl(video, "download");
        await refreshRecentVideos();
        window.location.assign(url);
    } catch (error) {
        console.error("Recent video download error:", error);
        setRecentVideosStatus(error.message || "Could not download this video.");
    }
}

async function deleteRecentVideo(video) {
    if (!window.confirm("Delete this video from Recent Videos and remove its local file?")) return;
    try {
        const { data: { session }, error } = await window.supabaseClient.auth.getSession();
        if (error) throw error;
        if (!session) throw new Error("Sign in again to delete this video.");
        const response = await fetch(`${API_BASE_URL}/history/${encodeURIComponent(video.id)}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not delete this video.");
        if (generatedVideos.some((item) => item.filename === video.filename)) {
            showGeneratedVideos(generatedVideos.filter((item) => item.filename !== video.filename));
        }
        setRecentVideosStatus("");
        await refreshRecentVideos();
    } catch (error) {
        console.error("Recent video delete error:", error);
        setRecentVideosStatus(error.message || "Could not delete this video.");
    }
}

async function renderCurrentVideo() {
    const video = generatedVideos[currentVideoIndex];
    if (!video) return;
    const player = document.getElementById("rendered-video-player");
    const previewBox = document.getElementById("video-preview-box");
    const filename = document.getElementById("preview-filename");
    const counter = document.getElementById("preview-counter");
    const downloadButton = document.getElementById("download-btn");
    const caption = document.getElementById("preview-caption");
    const previousButton = document.getElementById("preview-previous");
    const nextButton = document.getElementById("preview-next");
    const gallery = document.getElementById("video-gallery");

    if (player) player.pause();
    if (downloadButton) downloadButton.removeAttribute("href");
    if (filename) filename.textContent = video.filename || `Reel ${currentVideoIndex + 1}`;
    if (counter) counter.textContent = `${currentVideoIndex + 1} / ${generatedVideos.length}`;
    if (downloadButton) downloadButton.download = video.filename || "noor-ul-quran-reel.mp4";
    if (caption) caption.textContent = video.caption || "";
    if (previousButton) previousButton.disabled = currentVideoIndex === 0;
    if (nextButton) nextButton.disabled = currentVideoIndex === generatedVideos.length - 1;
    if (gallery) {
        gallery.replaceChildren(...generatedVideos.map((item, index) => {
            const button = document.createElement("button");
            const range = item.start_ayah === item.end_ayah
                ? `${item.start_ayah ?? ""}`
                : `${item.start_ayah ?? ""}-${item.end_ayah ?? ""}`;
            button.type = "button";
            button.setAttribute("aria-label", `Show Surah ${item.surah ?? ""}, verses ${range}`);
            button.setAttribute("aria-current", index === currentVideoIndex ? "true" : "false");
            button.title = item.filename || `Reel ${index + 1}`;
            button.className = `shrink-0 max-w-56 truncate rounded-md border px-3 py-2 text-xs font-semibold ${index === currentVideoIndex ? "border-amber-400 bg-amber-500/15 text-amber-200" : "border-slate-700 bg-slate-950 text-slate-300 hover:border-slate-500"}`;
            button.textContent = `Surah ${item.surah ?? ""} · ${range}`;
            button.addEventListener("click", () => {
                currentVideoIndex = index;
                renderCurrentVideo();
            });
            return button;
        }));
    }
    if (previewBox) previewBox.setAttribute("aria-label", `Reel ${currentVideoIndex + 1} of ${generatedVideos.length}`);

    try {
        const videoUrl = await resolveVideoUrl(video);
        const currentVideo = generatedVideos[currentVideoIndex];
        if (!currentVideo || currentVideo.filename !== video.filename) return;
        if (player) {
            player.src = videoUrl;
            player.preload = "metadata";
            player.load();
            const lengthLabel = document.getElementById("download-length");
            if (lengthLabel) lengthLabel.textContent = "";
            player.onloadedmetadata = () => {
                if (lengthLabel && Number.isFinite(player.duration)) lengthLabel.textContent = `Length: ${Math.round(player.duration)} s`;
            };
        }
        if (downloadButton) downloadButton.href = videoUrl;
    } catch (error) {
        const currentVideo = generatedVideos[currentVideoIndex];
        if (!currentVideo || currentVideo.filename !== video.filename) return;
        console.error("Secure video URL error:", error);
        if (filename) filename.textContent = `${video.filename || "Video"} — ${error.message}`;
    }
}

function showPreviousVideo() {
    if (currentVideoIndex <= 0) return;
    currentVideoIndex -= 1;
    renderCurrentVideo();
}

function showNextVideo() {
    if (currentVideoIndex >= generatedVideos.length - 1) return;
    currentVideoIndex += 1;
    renderCurrentVideo();
}

function setBatchDownload(jobId, videos) {
    const button = document.getElementById("download-all-btn");
    if (!button) return;
    latestBatchJobId = typeof jobId === "string" && videos.length > 1 ? jobId : null;
    button.classList.toggle("hidden", !latestBatchJobId);
}

async function downloadAllGeneratedVideos() {
    const button = document.getElementById("download-all-btn");
    if (!button || !latestBatchJobId || button.disabled) return;
    const originalText = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner animate-spin" aria-hidden="true"></i>Preparing ZIP...';
    try {
        const { data: { session }, error } = await window.supabaseClient.auth.getSession();
        if (error) throw error;
        if (!session) throw new Error("Sign in again to download this batch.");
        const response = await fetch(
            `${API_BASE_URL}/generate/${encodeURIComponent(latestBatchJobId)}/download-url`,
            { headers: { Authorization: "Bearer " + session.access_token } }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not prepare the ZIP download.");
        const downloadUrl = new URL(result.url, new URL(API_BASE_URL, window.location.origin).origin);
        window.location.assign(downloadUrl.href);
    } catch (error) {
        console.error("Bulk reel download error:", error);
        const statusBox = document.getElementById("status-box");
        if (statusBox) {
            statusBox.innerText = "Download error: " + (error.message || "Could not download the reel ZIP.");
            statusBox.style.color = "#dc2626";
        }
    } finally {
        button.disabled = false;
        button.innerHTML = originalText;
    }
}

document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target?.matches?.("input, textarea, select, video, [contenteditable='true']")) return;
    if (event.key === "ArrowLeft") showPreviousVideo();
    if (event.key === "ArrowRight") showNextVideo();
});

function setMode(mode) {
    const allowedMode = ["pick", "picks", "random"].includes(mode) ? mode : "pick";
    const hasPremium = Boolean(window.userAccess?.is_admin || window.userAccess?.premium_access);
    const locked = allowedMode !== "pick" && !hasPremium;
    const modeSelect = document.getElementById("mode-select");
    if (modeSelect) modeSelect.value = allowedMode;

    ["pick", "picks", "random"].forEach((name) => {
        document.getElementById(`mode-${name}`)?.classList.toggle("hidden", name !== allowedMode || locked);
        const button = document.getElementById(`btn-${name}`);
        if (button) {
            button.classList.toggle("bg-slate-800", name === allowedMode && !locked);
            button.classList.toggle("border", name === allowedMode && !locked);
            button.classList.toggle("border-amber-500/30", name === allowedMode && !locked);
            button.classList.toggle("text-amber-400", name === allowedMode && !locked);
            button.classList.toggle("text-slate-400", name !== allowedMode || locked);
        }
    });
    document.querySelectorAll("#btn-picks .fa-lock, #btn-random .fa-lock").forEach((icon) => {
        icon.classList.toggle("hidden", hasPremium);
    });
    document.getElementById("locked-pro-notice")?.classList.toggle("hidden", !locked);
    if (allowedMode === "picks" && !locked) window.NoorPicks?.show();
    const submitButton = document.getElementById("generate-button");
    if (submitButton) submitButton.disabled = locked || limitReached();
}

async function handleFormSubmit(event) {
    event.preventDefault();
    await generateVideo(event);
}

async function copyCaption() {
    const caption = generatedVideos[currentVideoIndex]?.caption || "Generated with Noor ul Quran Auto Reel Maker #QuranReels #IslamicContent";
    try {
        await navigator.clipboard.writeText(caption);
        alert("Caption copied to clipboard!");
    } catch (error) {
        console.error("Clipboard error:", error);
        alert("Could not copy the caption. Check browser clipboard permissions.");
    }
}

// ---- Ayah image: theme + suggestions (refs only; text always comes from the server) ----
let featuredList = [];
const RECENT_FEATURED_KEY = "noorRecentFeatured";

function recentFeatured() {
    try { return JSON.parse(localStorage.getItem(RECENT_FEATURED_KEY)) || []; } catch (e) { return []; }
}

function rememberFeatured(ref) {
    try {
        const list = [ref, ...recentFeatured().filter((r) => r !== ref)].slice(0, 10);
        localStorage.setItem(RECENT_FEATURED_KEY, JSON.stringify(list));
    } catch (e) { /* storage unavailable */ }
}

function pickRefs(count) {
    const theme = document.getElementById("image-theme")?.value;
    const refs = featuredList.filter((item) => !theme || item.theme === theme).map((item) => item.ref);
    const recent = recentFeatured();
    const fresh = refs.filter((r) => !recent.includes(r));
    const pool = (fresh.length >= count ? fresh : refs).slice();
    const picked = [];
    while (picked.length < count && pool.length) {
        picked.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
    }
    return picked;
}

// Empty input: pick here so "random" respects the chosen theme and the last-10 memory.
function ayahToSend(reference) {
    const ref = reference || pickRefs(1)[0];
    if (!ref) return {};
    rememberFeatured(ref);
    return { ayah: ref };
}

async function initializeImageSuggestions() {
    const select = document.getElementById("image-theme");
    const chips = document.getElementById("image-suggestions");
    if (!select || !chips) return;
    try {
        const response = await fetch(`${API_BASE_URL}/featured`);
        featuredList = (await response.json()).ayahs || [];
    } catch (e) { return; }
    [...new Set(featuredList.map((item) => item.theme))].sort().forEach((theme) => {
        const option = document.createElement("option");
        option.value = theme;
        option.textContent = theme;
        select.appendChild(option);
    });
    document.getElementById("image-suggest-button")?.addEventListener("click", () => {
        chips.replaceChildren(...pickRefs(5).map((ref) => {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.textContent = ref;
            chip.className = "rounded-full border border-slate-500 px-3 py-1.5 font-mono text-xs font-semibold text-slate-100 hover:bg-slate-800";
            chip.addEventListener("click", () => {
                document.getElementById("image-ayah").value = ref;
            });
            return chip;
        }));
    });
}

// ---- Video: suggested ranges ----
let suggestTimer = null;

function scheduleRangeSuggestions() {
    window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(loadRangeSuggestions, 500);
}

async function loadRangeSuggestions() {
    const box = document.getElementById("range-suggestions");
    if (!box) return;
    const { surah, start } = currentEstimateRange();
    if (!surah) return;
    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) return;
        const mode = document.getElementById("input-content-mode")?.value || "full";
        const params = new URLSearchParams({ surah: String(surah), mode, from_ayah: String(start || 1) });
        const reciter = document.getElementById("input-reciter")?.value;
        const urdu = document.getElementById("input-urdu-voice")?.value;
        if (reciter) params.set("reciter_id", reciter);
        if (urdu && mode !== "arabic_only") params.set("urdu_voice_id", urdu);
        const response = await fetch(`${API_BASE_URL}/suggest-ranges?${params}`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Suggestions unavailable.");
        box.replaceChildren(...result.suggestions.map((item) => {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.dataset.target = item.target;
            chip.dataset.origin = item.origin;
            chip.textContent = item.origin === "selected" && item.start ? `${item.label} [from ${item.start}]` : item.label;
            if (!item.allowed && item.start) chip.textContent += ` - ${item.reason}`;
            chip.disabled = !item.allowed;
            if (item.reason) chip.title = item.reason;
            chip.className = "rounded-full border border-slate-500 px-3 py-1.5 text-xs font-semibold text-slate-100 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50";
            chip.addEventListener("click", () => {
                document.getElementById("estimate-start").value = item.start;
                document.getElementById("estimate-end").value = item.end;
                syncRangeText();
                scheduleDurationEstimate();
            });
            return chip;
        }));
    } catch (error) {
        box.textContent = error.message || "Suggestions unavailable.";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    if (!document.getElementById("range-suggestions")) return;
    ["estimate-surah", "estimate-start", "input-content-mode", "input-reciter", "input-urdu-voice"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", scheduleRangeSuggestions);
    });
    scheduleRangeSuggestions();
});
