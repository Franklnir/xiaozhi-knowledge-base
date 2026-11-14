// Xiaozhi AI Service Worker for PWA Offline Resilience & Instant Shell Caching
const CACHE_NAME = 'xiaozhi-pwa-v7';
const PRECACHE_ASSETS = [
  '/static/css/app.min.css',
  '/static/img/apple-touch-icon.png',
  '/static/img/favicon-32x32.png',
  '/static/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('[PWA-SW] Precaching some assets skipped:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key.startsWith('xiaozhi-pwa-') && key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Cache public assets only. Authenticated pages and request IDs are per response.
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/static/')) return;

  // Network-first with cache fallback for public assets.
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 &&
            !/\b(?:private|no-store)\b/i.test(networkResponse.headers.get('Cache-Control') || '')) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache).catch(() => {});
          });
        }
        return networkResponse;
      })
      .catch(() => {
        return caches.open(CACHE_NAME).then((cache) => cache.match(event.request));
      })
  );
});
