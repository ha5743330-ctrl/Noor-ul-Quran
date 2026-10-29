// Supabase Client Setup - Verified URL and Key
const supabaseConfig = window.NOOR_SUPABASE_CONFIG || {};
const SUPABASE_URL = supabaseConfig.url || "";
const SUPABASE_ANON_KEY = supabaseConfig.anonKey || "";

let supabaseClient = null;
if (typeof supabase !== 'undefined' && SUPABASE_URL && SUPABASE_ANON_KEY) {
    supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}
window.supabaseClient = supabaseClient;

function getPostAuthRedirect() {
    const next = new URLSearchParams(window.location.search).get("next");
    if (!next || !next.startsWith("/") || next.startsWith("//")) return "/";
    const target = new URL(next, window.location.origin);
    if (target.origin !== window.location.origin || target.pathname.startsWith("/auth")) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
}

function getAuthCallbackUrl() {
    return `${window.location.origin}/auth.html?next=${encodeURIComponent(getPostAuthRedirect())}`;
}

document.addEventListener("DOMContentLoaded", async () => {
    if (!supabaseClient || !["/auth", "/auth.html"].includes(window.location.pathname)) return;
    const { data: { session } } = await supabaseClient.auth.getSession();
    if (session) window.location.replace(getPostAuthRedirect());
});

// Helper function to show alert messages inside auth.html
function showAlert(message, isError = true) {
    const alertBox = document.getElementById("auth-alert");
    if (!alertBox) {
        alert(message);
        return;
    }
    alertBox.innerText = message;
    alertBox.className = isError
        ? "p-3 rounded-xl text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20 block"
        : "p-3 rounded-xl text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 block";
}

function showResendConfirmation(show) {
    document.getElementById("btn-resend-confirmation")?.classList.toggle("hidden", !show);
}

// 1. Sign In Handler
async function handleSignIn(event) {
    if (event) event.preventDefault();

    const email = document.getElementById("login-email")?.value.trim();
    const password = document.getElementById("login-password")?.value;
    const submitBtn = document.getElementById("btn-login");

    if (!email || !password) {
        showAlert("Please enter both email and password.");
        return;
    }

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Signing in...`;
    }

    try {
        if (!supabaseClient) throw new Error("Supabase is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY to .env, then restart the server.");
        const { data, error } = await supabaseClient.auth.signInWithPassword({
            email: email,
            password: password
        });

        if (error) {
            showAlert(error.message, true);
            showResendConfirmation(error.code === "email_not_confirmed" || /email not confirmed/i.test(error.message));
        } else {
            showResendConfirmation(false);
            showAlert("Login Successful! Redirecting...", false);
            setTimeout(() => { window.location.href = getPostAuthRedirect(); }, 1000);
        }
    } catch (err) {
        const message = err instanceof TypeError
            ? "Could not reach Supabase. Check your internet connection and try again."
            : (err.message || "Sign in failed. Please try again.");
        showAlert(message, true);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<i class="fa-solid fa-right-to-bracket"></i> Sign In`;
        }
    }
}

// 2. Sign Up Handler
async function handleSignUp(event) {
    if (event) event.preventDefault();

    const fullName = document.getElementById("signup-name")?.value.trim();
    const email = document.getElementById("signup-email")?.value.trim();
    const password = document.getElementById("signup-password")?.value;
    const submitBtn = document.getElementById("btn-signup");

    if (!fullName || !email || !password) {
        showAlert("Please fill in all fields.");
        return;
    }
    if (!document.getElementById("terms")?.checked) {
        showAlert("Please agree to the Terms and Privacy Policy to continue.");
        return;
    }

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Creating account...`;
    }

    try {
        if (!supabaseClient) throw new Error("Supabase is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY to .env, then restart the server.");
        const { data, error } = await supabaseClient.auth.signUp({
            email: email,
            password: password,
            options: {
                data: { full_name: fullName },
                emailRedirectTo: getAuthCallbackUrl()
            }
        });

        if (error) {
            showAlert(error.message, true);
        } else {
            if (data.session) {
                showAlert("Account created. Opening your studio...", false);
                window.location.href = getPostAuthRedirect();
            } else {
                showAlert("Account created. Check your email to confirm the account, then sign in.", false);
            }
        }
    } catch (err) {
        const message = err instanceof TypeError
            ? "Could not reach Supabase. Check your internet connection and try again."
            : (err.message || "Account creation failed. Please try again.");
        showAlert(message, true);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<i class="fa-solid fa-user-plus"></i> Create Free Account`;
        }
    }
}

async function resendConfirmationEmail() {
    const email = document.getElementById("login-email")?.value.trim()
        || document.getElementById("signup-email")?.value.trim();
    if (!email) {
        showAlert("Enter your email address first.");
        return;
    }

    const button = document.getElementById("btn-resend-confirmation");
    if (button) button.disabled = true;
    try {
        if (!supabaseClient) throw new Error("Supabase is not configured.");
        const { error } = await supabaseClient.auth.resend({
            type: "signup",
            email,
            options: { emailRedirectTo: getAuthCallbackUrl() }
        });
        if (error) throw error;
        showResendConfirmation(false);
        showAlert("Confirmation email sent. Check your inbox and spam folder.", false);
    } catch (error) {
        showAlert(error.message || "Could not resend confirmation email.", true);
    } finally {
        if (button) button.disabled = false;
    }
}

// 3. Social OAuth Handlers
async function handleSocialAuth(provider) {
    try {
        if (!supabaseClient) throw new Error("Supabase client unavailable.");
        const { data, error } = await supabaseClient.auth.signInWithOAuth({
            provider: provider,
            options: { redirectTo: new URL(getPostAuthRedirect(), window.location.origin).href }
        });
        if (error) showAlert(error.message, true);
    } catch (err) {
        showAlert(`Error signing in with ${provider}: ${err.message}`, true);
    }
}

// 4. Password Reset
function showForgot(event) {
    if (event) event.preventDefault();
    document.getElementById("form-login")?.classList.add("hidden");
    document.getElementById("form-signup")?.classList.add("hidden");
    document.getElementById("form-forgot")?.classList.remove("hidden");
    document.getElementById("auth-title").innerText = "Reset Password";
    document.getElementById("auth-subtitle").innerText = "We will email you a reset link";
    document.getElementById("auth-alert")?.classList.add("hidden");
}

let resetCooldownTimer = null;
async function handleForgot(event) {
    event.preventDefault();
    const button = document.getElementById("btn-forgot");
    const email = document.getElementById("forgot-email").value.trim();
    if (!email || button.disabled) return;
    try {
        if (!supabaseClient) throw new Error("Supabase client unavailable.");
        const { error } = await supabaseClient.auth.resetPasswordForEmail(email, {
            redirectTo: `${window.location.origin}/reset-password.html`
        });
        if (error) console.warn("Password reset request failed:", error.message);
    } catch (err) {
        console.warn("Password reset request failed:", err.message);
    }
    // Same message whether or not the account exists (no account enumeration).
    showAlert("If this email exists, a reset link was sent. Check your inbox and spam folder.", false);
    let left = 60;
    button.disabled = true;
    button.textContent = `Wait ${left}s`;
    clearInterval(resetCooldownTimer);
    resetCooldownTimer = setInterval(() => {
        left -= 1;
        button.textContent = left > 0 ? `Wait ${left}s` : "Send reset link";
        if (left <= 0) {
            clearInterval(resetCooldownTimer);
            button.disabled = false;
        }
    }, 1000);
}

// 5. Sign Out Function
async function handleSignOut(event) {
    if (event) event.preventDefault();
    if (supabaseClient) await supabaseClient.auth.signOut();
    sessionStorage.clear();
    window.location.href = "auth.html";
}