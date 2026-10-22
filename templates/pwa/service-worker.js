'use strict';
const CACHE = 'qicko-offline-v1';
const OFFLINE = '{% url "offline" %}';
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const response = await fetch(OFFLINE, {cache: 'reload', credentials: 'omit'});
    if (!response.ok || response.redirected) throw new Error('Offline page unavailable');
    const cache = await caches.open(CACHE);
    await cache.put(OFFLINE, response);
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith('qicko-offline-') && name !== CACHE)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  // Never cache HTML from the network, books, photos, media, API data or POSTs.
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate' ||
      new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith((async () => {
    try { return await fetch(event.request); }
    catch {
      return await caches.match(OFFLINE, {cacheName: CACHE}) ||
        new Response('Нет подключения к интернету. Попробуйте ещё раз.',
          {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
    }
  })());
});
