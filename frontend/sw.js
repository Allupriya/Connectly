// Service worker: shows Web Push notifications even when the app is closed.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: "New message", body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "ChatSystem";
  const options = {
    body: payload.body || "",
    icon: payload.icon || "/icon.png",
    badge: payload.badge || "/icon.png",
    // Same chat -> notifications collapse into one instead of stacking.
    tag: payload.chat_id || "chat",
    renotify: true,
    data: { url: payload.url || "/", chat_id: payload.chat_id || null },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        // Focus an already-open tab if we have one.
        for (const client of clientList) {
          if ("focus" in client) return client.focus();
        }
        // Otherwise open a new one.
        if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
      })
  );
});
