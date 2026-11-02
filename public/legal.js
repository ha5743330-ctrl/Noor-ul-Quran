// CONTACT_EMAIL comes from config.js (env var CONTACT_EMAIL). No placeholder address is ever shown.
document.addEventListener("DOMContentLoaded", () => {
    const email = String(window.NOOR_CONTACT_EMAIL || "").trim();
    if (!/^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(email)) return;
    document.querySelectorAll(".contact-email").forEach((link) => {
        link.textContent = email;
        link.href = `mailto:${email}`;
    });
    document.querySelectorAll(".contact-line").forEach((line) => {
        line.hidden = false;
    });
});
