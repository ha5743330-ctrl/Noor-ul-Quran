// Minimal PWA worker: caches the static shell only. /api and every non-GET request go straight to the network.
const CACHE = "noor-shell-v1";
const SHELL = ["/offline.html", "/ui.css", "/nav.js", "/pwa.js", "/assets/logo.png", "/assets/icon-192.png"];

self.addEventListener("install", (e) => {
    e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
    e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
        .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
    const { request } = e;
    const url = new URL(request.url);
    if (request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")
        || url.pathname === "/config.js" || request.headers.has("range")) return;  // not ours: browser handles it
    if (request.mode === "navigate") {  // pages: network first, offline page when the network is down
        e.respondWith(fetch(request).catch(() => caches.match("/offline.html")));
        return;
    }
    if (SHELL.includes(url.pathname)) {  // shell assets: network first (never stale after a deploy), cache when offline
        e.respondWith(fetch(request).then((res) => {
            if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(request, copy)); }
            return res;
        }).catch(() => caches.match(request)));
    }
});
