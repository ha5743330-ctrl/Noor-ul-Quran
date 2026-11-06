// Registers the service worker and exposes the install prompt to nav.js (button shows only when the browser offers install).
if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    window.noorInstallPrompt = e;
    window.dispatchEvent(new Event("noor-install-ready"));
});
window.addEventListener("appinstalled", () => {
    window.noorInstallPrompt = null;
    document.querySelectorAll(".pwa-install").forEach((b) => { b.hidden = true; });
});
