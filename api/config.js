module.exports = function configHandler(req, res) {
    const config = {
        url: process.env.SUPABASE_URL || "",
        anonKey: process.env.SUPABASE_ANON_KEY || "",
        apiBaseUrl: process.env.API_BASE_URL || "/api"
    };

    res.setHeader("Content-Type", "application/javascript; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.status(200).send(
        `window.NOOR_SUPABASE_CONFIG = ${JSON.stringify({ url: config.url, anonKey: config.anonKey })};` +
        `window.NOOR_API_BASE_URL = ${JSON.stringify(config.apiBaseUrl.replace(/\/$/, ""))};`
    );
};
