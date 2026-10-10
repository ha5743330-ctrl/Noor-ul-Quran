const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
window.userAccess = null;
window.videoStyles = {};
let generatedVideos = [];
let currentVideoIndex = 0;
let estimateTimer = null;
let estimateRequestId = 0;
let lastEstimateRangeSeconds = 0;

const VIDEO_STYLE_LABELS = {
    bismillah: "Bismillah",
    surah: "Surah Name",
    label: "Ayat Range",
    arabic: "Arabic Ayaat",
    urdu: "Urdu Tarjuma",
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
    initializeVideoStyleControls();
    initializeDurationEstimate();
    initializeStudio();
});

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
    document.getElementById("input-content-mode")?.addEventListener("change", scheduleDurationEstimate);
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
        item.textContent = `${VIDEO_STYLE_LABELS[element]}: ${style.color} · ${VIDEO_STYLE_FONT_LABELS[style.font]}`;
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

function initializeVideoStyleControls() {
    document.getElementById("apply-video-style")?.addEventListener("click", () => {
        const element = document.getElementById("video-style-element")?.value;
        const color = document.getElementById("video-style-color")?.value;
        const font = document.getElementById("video-style-font")?.value;
        if (!element || !color || !font) return;
        window.videoStyles[element] = { color, font };
        renderAppliedVideoStyles();
    });
    document.getElementById("reset-video-styles")?.addEventListener("click", () => {
        window.videoStyles = {};
        renderAppliedVideoStyles();
    });
}

async function initializeStudio() {
    const client = window.supabaseClient;
    if (!client) {
        window.location.replace("/auth.html");
        return;
    }

    const { data: { session }, error } = await client.auth.getSession();
    if (error || !session) {
        window.location.replace("/auth.html");
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/access`, {
            headers: { Authorization: `Bearer ${session.access_token}` }
        });
        if (!response.ok) {
            if (response.status === 401) {
                await client.auth.signOut();
                window.location.replace("/auth.html");
                return;
            }
            const result = await response.json();
            throw new Error(result.detail || "Could not load account access.");
        }
        window.userAccess = await response.json();
    } catch (error) {
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
        authButton.textContent = session.user.email || "Sign Out";
        authButton.href = "#";
        authButton.onclick = handleSignOut;
    }
    if (adminLink) adminLink.classList.toggle("hidden", !window.userAccess.is_admin);
    const hasPremium = Boolean(window.userAccess.is_admin || window.userAccess.premium_access);
    document.querySelectorAll("#input-max-dur option").forEach((option) => {
        option.disabled = !hasPremium && Number(option.value) > 60;
    });
    setMode("pick");
}
// 2. Video Generation Function with Preview Link
async function generateVideo(event) {
    if (event) event.preventDefault();

    // Inputs
    const mode = document.getElementById("mode-select")?.value || "pick";
    const verse = document.getElementById("input-pick")?.value || document.getElementById("verse-input")?.value || "";
    const duration = document.getElementById("input-max-dur")?.value || document.getElementById("duration-input")?.value || 60;

    // UI Elements
    const statusBox = document.getElementById("status-box");
    const outputBox = document.getElementById("output-box");
    const logMsg = document.getElementById("log-message");
    const statusPercent = document.getElementById("status-percent");
    const generateButton = document.getElementById("generate-button");

    // Validation
    if (mode === "pick" && !verse) {
        alert("Please enter a Surah:Verse range (e.g., 55:1-8)");
        return;
    }
    if (generateButton?.disabled) return;

    // Show Terminal Progress Box
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
    renderGenerationWarnings([]);

    try {
        const { data: { session } } = await window.supabaseClient.auth.getSession();
        if (!session) {
            window.location.replace("/auth.html");
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
                quality: document.getElementById("input-quality")?.value || "balanced",
                content_mode: document.getElementById("input-content-mode")?.value || "full",
                styles: window.videoStyles
            })
        });

        let result = await response.json().catch(() => ({ detail: `Server returned HTTP ${response.status}.` }));
        if (response.status === 202 && result.job_id) {
            result = await waitForGeneration(result.job_id, session.access_token, statusBox, logMsg, statusPercent);
        }

        if (response.ok && result.status !== "failed") {
            if (logMsg) logMsg.innerText = "✓ " + (result.message || "Video rendering completed.");
            if (statusPercent) statusPercent.innerText = "100%";
            if (statusBox) {
                statusBox.innerText = result.status === "partial"
                    ? "⚠ Some reels could not be rendered. Completed reels are available below."
                    : `✅ ${result.videos?.length || 1} reel(s) ready to preview.`;
                statusBox.style.color = result.status === "partial" ? "#d97706" : "#059669";
            }
            const videos = Array.isArray(result.videos) ? result.videos : result.filename ? [result] : [];
            renderGenerationWarnings(result.warnings || videos.flatMap((video) => video.warnings || []));
            showGeneratedVideos(videos);
        } else {
            if (result.videos?.length) showGeneratedVideos(result.videos);
            const errorMessage = result.error || result.detail || "Generation failed.";
            if (logMsg) logMsg.innerText = "❌ Error: " + errorMessage;
            if (statusPercent) statusPercent.innerText = "0%";
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
        if (statusBox) {
            statusBox.innerText = "❌ " + errorMessage;
            statusBox.style.color = "#dc2626";
        }
    } finally {
        if (generateButton) {
            generateButton.removeAttribute("aria-busy");
            const hasPremium = Boolean(window.userAccess?.is_admin || window.userAccess?.premium_access);
            const currentMode = document.getElementById("mode-select")?.value || "pick";
            generateButton.disabled = currentMode !== "pick" && !hasPremium;
        }
    }
}

async function waitForGeneration(jobId, accessToken, statusBox, logMsg, statusPercent) {
    let lastVideoCount = 0;
    while (true) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const response = await fetch(`${API_BASE_URL}/generate/${encodeURIComponent(jobId)}`, {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        const job = await response.json().catch(() => ({ error: `Status check returned HTTP ${response.status}.` }));
        if (!response.ok) throw new Error(job.detail || job.error || "Could not read generation status.");

        const videos = Array.isArray(job.videos) ? job.videos : [];
        renderGenerationWarnings(job.warnings || videos.flatMap((video) => video.warnings || []));
        if (videos.length) showGeneratedVideos(videos);
        if (videos.length !== lastVideoCount) {
            lastVideoCount = videos.length;
            if (statusPercent) statusPercent.innerText = `${lastVideoCount} ready`;
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
    const selectedIndex = generatedVideos.findIndex((video) => video.filename === selectedFilename);
    currentVideoIndex = selectedIndex >= 0 ? selectedIndex : Math.min(currentVideoIndex, generatedVideos.length - 1);
    const previewBox = document.getElementById("video-preview-box");
    if (!generatedVideos.length || !previewBox) return;
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
        if (generatedVideos[currentVideoIndex] !== video) return;
        if (player) {
            player.src = videoUrl;
            player.load();
        }
        if (downloadButton) downloadButton.href = videoUrl;
    } catch (error) {
        if (generatedVideos[currentVideoIndex] !== video) return;
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

document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches("input, textarea, select, video, [contenteditable='true']")) return;
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
    const submitButton = document.getElementById("generate-button");
    if (submitButton) submitButton.disabled = locked;
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