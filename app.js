const API_BASE_URL = (window.NOOR_API_BASE_URL || window.NOOR_SUPABASE_CONFIG?.apiBaseUrl || "/api").replace(/\/$/, "");
window.userAccess = null;

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

    // Validation
    if (mode === "pick" && !verse) {
        alert("Please enter a Surah:Verse range (e.g., 55:1-8)");
        return;
    }

    // Show Terminal Progress Box
    if (outputBox) outputBox.classList.remove("hidden");
    if (logMsg) logMsg.innerText = "> Sending request to Python FFmpeg Rendering Engine...";
    if (statusPercent) statusPercent.innerText = "25%";

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
                random_count: parseInt(document.getElementById("input-random-count")?.value || "1", 10)
            })
        });

        const result = await response.json();

        if (response.ok) {
            if (logMsg) logMsg.innerText = "✓ " + (result.message || "Video rendered successfully!");
            if (statusPercent) statusPercent.innerText = "100%";
            if (statusBox) {
                statusBox.innerText = "✅ Video generation complete!";
                statusBox.style.color = "#059669";
            }

            // Video Preview & Download Setup
            const videoPreviewBox = document.getElementById("video-preview-box");
            const videoPlayer = document.getElementById("rendered-video-player");
            const videoSource = document.getElementById("video-source");
            const downloadBtn = document.getElementById("download-btn");
            const previewFilename = document.getElementById("preview-filename");

            const filename = result.filename || `001_s${verse.replace(':', '_')}.mp4`;

            if (videoPreviewBox && videoPlayer) {
                const apiOrigin = new URL(API_BASE_URL, window.location.origin).origin;
                const videoUrl = new URL(result.url || `/output/${encodeURIComponent(filename)}`, apiOrigin);

                if (videoSource) videoSource.src = videoUrl.href;
                if (downloadBtn) downloadBtn.href = videoUrl.href;
                if (previewFilename) previewFilename.innerText = filename;

                videoPlayer.load();
                videoPreviewBox.classList.remove("hidden");
            }
        } else {
            if (logMsg) logMsg.innerText = "❌ Error: " + (result.detail || "Generation failed.");
            if (statusPercent) statusPercent.innerText = "0%";
            if (statusBox) {
                statusBox.innerText = "❌ Error: " + (result.detail || "Generation failed.");
                statusBox.style.color = "#dc2626";
            }
        }
    } catch (error) {
        console.error("API Error:", error);
        if (logMsg) logMsg.innerText = "❌ Unable to connect to FastAPI Backend Server.";
        if (statusPercent) statusPercent.innerText = "0%";
        if (statusBox) {
            statusBox.innerText = "❌ Connection failed. Make sure server is running.";
            statusBox.style.color = "#dc2626";
        }
    }
}

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

function copyCaption() {
    navigator.clipboard.writeText("Generated with Noor ul Quran Auto Reel Maker #QuranReels #IslamicContent");
    alert("Captions copied to clipboard!");
}