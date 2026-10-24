// Xiaozhi AI Service Worker for PWA Offline Resilience & Instant Shell Caching
const CACHE_NAME = 'xiaozhi-pwa-v1';
const PRECACHE_ASSETS = [
  '/',
  '/static/css/app.min.css',
  '/static/js/app.js',
  '/static/img/logo.png',
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
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Skip SSE streams, WebSocket endpoints, API mutations, and device polling
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/mcp') ||
    url.pathname.includes('/stream') ||
    url.pathname.includes('/sse') ||
    url.pathname.includes('/download')
  ) {
    return;
  }

  // Network-first with cache fallback for HTML pages and assets
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache).catch(() => {});
          });
        }
        return networkResponse;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});
