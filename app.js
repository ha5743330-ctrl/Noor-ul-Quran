const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
window.userAccess = null;
let generatedVideos = [];
let currentVideoIndex = 0;

document.addEventListener("DOMContentLoaded", initializeStudio);

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
                quality: document.getElementById("input-quality")?.value || "balanced"
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
    generatedVideos = videos;
    const selectedIndex = generatedVideos.findIndex((video) => video.filename === selectedFilename);
    currentVideoIndex = selectedIndex >= 0 ? selectedIndex : Math.min(currentVideoIndex, generatedVideos.length - 1);
    const previewBox = document.getElementById("video-preview-box");
    if (!generatedVideos.length || !previewBox) return;
    previewBox.classList.remove("hidden");
    renderCurrentVideo();
    if (wasEmpty) previewBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function renderCurrentVideo() {
    const video = generatedVideos[currentVideoIndex];
    if (!video) return;
    const player = document.getElementById("rendered-video-player");
    const apiOrigin = new URL(API_BASE_URL, window.location.origin).origin;
    const fallbackUrl = `/output/${encodeURIComponent(video.filename)}`;
    const videoUrl = new URL(video.url || fallbackUrl, apiOrigin);
    const previewBox = document.getElementById("video-preview-box");
    const filename = document.getElementById("preview-filename");
    const counter = document.getElementById("preview-counter");
    const downloadButton = document.getElementById("download-btn");
    const caption = document.getElementById("preview-caption");
    const previousButton = document.getElementById("preview-previous");
    const nextButton = document.getElementById("preview-next");
    const gallery = document.getElementById("video-gallery");

    if (player) {
        player.pause();
        player.src = videoUrl.href;
        player.load();
    }
    if (filename) filename.textContent = video.filename || `Reel ${currentVideoIndex + 1}`;
    if (counter) counter.textContent = `${currentVideoIndex + 1} / ${generatedVideos.length}`;
    if (downloadButton) {
        downloadButton.href = videoUrl.href;
        downloadButton.download = video.filename || "noor-ul-quran-reel.mp4";
    }
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