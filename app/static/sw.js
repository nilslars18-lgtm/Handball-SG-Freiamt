// Service Worker der Team-App: schneller Start, Offline-Hinweis und Push-Benachrichtigungen.
const VERSION = "v1";
const CACHE = "sgf-u19-" + VERSION;
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/badge-96.png", "/logo"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Seite: zuerst Netz (immer aktuelle Version), bei fehlendem Netz die gespeicherte.
// Icons/Logo: aus dem Speicher (schneller Start). Daten (API): nie zwischenspeichern.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    // Wenn der Server schlaeft, nach 3 Sekunden die gespeicherte Seite zeigen
    // (die Daten laden dann nach, sobald er wach ist).
    const net = fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put("/", copy)); }
      return res;
    });
    const fallback = new Promise((resolve) => setTimeout(() => caches.match("/").then(resolve), 3000));
    event.respondWith(
      Promise.race([net.catch(() => caches.match("/")), fallback.then((r) => r || net)])
        .then((r) => r || net)
    );
    return;
  }
  if (/\.(png|webmanifest)$/.test(url.pathname) || url.pathname === "/logo") {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res;
      }))
    );
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "SG Freiamt U19", body: event.data && event.data.text() }; }
  const title = data.title || "SG Freiamt U19";
  const opts = {
    body: data.body || "",
    icon: "/icon-192.png",
    badge: "/badge-96.png",
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || "/" },
  };
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, opts),
    // Zaehler auf dem App-Symbol (wo unterstuetzt)
    self.navigator && self.navigator.setAppBadge ? self.navigator.setAppBadge().catch(() => {}) : Promise.resolve(),
  ]));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      if (new URL(c.url).origin === self.location.origin) {
        await c.focus();
        c.postMessage({ type: "open", url: target });
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});

self.addEventListener("pushsubscriptionchange", (event) => {
  // Browser hat das Abo erneuert -> die App meldet es beim naechsten Öffnen neu an.
  event.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => cs.forEach((c) => c.postMessage({ type: "resubscribe" }))));
});
