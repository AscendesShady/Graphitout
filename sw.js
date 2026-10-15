/* Cache the public editor shell only. Diagram data stays in browser storage. */
const ROOT = new URL('./', self.location.href);
const PREFIX = 'graphitout-shell-' + encodeURIComponent(ROOT.pathname) + '-';
const CACHE = PREFIX + 'v3-ink-1';
const SHELL = ['./', './index.html', './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png', './icons/maskable-512.png'].map(path => new URL(path, ROOT).href);
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  // Existing windows keep their current code until closed; never reload an active drawing.
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith(PREFIX) && name !== CACHE).map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== ROOT.origin || url.search || !SHELL.includes(url.href)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(event.request);
      // Never cache a personalized MCP bridge response, even if hosted at the same path.
      if (response.ok && !response.headers.get('cache-control')?.includes('no-store')) await cache.put(event.request, response.clone());
      return response;
    } catch (error) {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      throw error;
    }
  })());
});
