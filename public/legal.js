document.addEventListener("DOMContentLoaded", () => {
    const email = window.NOOR_CONTACT_EMAIL || "contact@example.com";
    document.querySelectorAll(".contact-email").forEach((link) => {
        link.textContent = email;
        link.href = `mailto:${email}`;
    });
});
