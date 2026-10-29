// Only immutable, content-addressed world assets persist. Navigation and manifests
// keep their normal freshness rules so a new generator is discovered immediately.
const TILE_CACHE = 'turku3d-immutable-v1';
const MAX_BYTES = 192 * 1024 * 1024;
// A tile can reference hundreds of small shared facade maps. The byte budget
// remains primary; allow a complete route's maps to survive a revisit.
const MAX_FILES = 4096;
let writes = Promise.resolve(), inventory;

function immutableAsset(request) {
  const url = new URL(request.url);
  return request.method === 'GET' && !request.headers.has('range')
    && url.pathname.startsWith('/coarse/')
    && /[./][a-f0-9]{64}\.(?:glb|ktx2|bin|col|json)$/.test(url.pathname);
}

async function trimAssets(cache, entries, maxBytes = MAX_BYTES, maxFiles = MAX_FILES) {
  let bytes = [...entries.values()].reduce((sum, size) => sum + size, 0);
  for (const [url, size] of entries) {
    if (bytes <= maxBytes && entries.size <= maxFiles) break;
    await cache.delete(url); entries.delete(url); bytes -= size;
  }
}

async function storeAsset(cache, request, response) {
  inventory ??= cache.keys().then(async keys => new Map(await Promise.all(keys.map(async key =>
    [key.url, Number((await cache.match(key))?.headers.get('X-Turku3D-Cache-Bytes')) || MAX_BYTES]))));
  const entries = await inventory;
  if (entries.has(request.url)) return;
  // Brotli Content-Length is the wire size. Bound actual stored bytes instead.
  const body = await response.blob();
  if (body.size > MAX_BYTES) return;
  const headers = new Headers(response.headers);
  headers.delete('Content-Encoding'); headers.delete('Content-Length');
  headers.set('X-Turku3D-Cache-Bytes', String(body.size));
  await cache.put(request, new Response(body, { status: response.status, statusText: response.statusText, headers }));
  entries.set(request.url, body.size);
  await trimAssets(cache, entries);
}

async function cachedAsset(request) {
  let cache;
  try {
    cache = await caches.open(TILE_CACHE);
    const hit = await cache.match(request);
    if (hit) return { response: hit };
  } catch { /* Storage denial must never prevent normal streaming. */ }
  const response = await fetch(request);
  if (!cache || response.status !== 200 || response.type === 'opaque'
    || !/\bimmutable\b/i.test(response.headers.get('Cache-Control') ?? '')) return { response };
  const copy = response.clone();
  const saved = writes = writes.catch(() => {}).then(() => storeAsset(cache, request, copy)).catch(() => {});
  return { response, saved };
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const request = event.request;
  if (immutableAsset(request)) {
    const result = cachedAsset(request);
    event.respondWith(result.then(value => value.response));
    event.waitUntil(result.then(value => value.saved));
    return;
  }
  if (new URL(request.url).origin !== self.location.origin) return;
  if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') return;
  event.respondWith(fetch(request).then(response => {
    if (response.status === 0) return response;
    const headers = new Headers(response.headers);
    headers.set('Cross-Origin-Opener-Policy', 'same-origin');
    headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  }));
});
