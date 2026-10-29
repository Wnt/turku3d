// Only content-addressed world assets persist. Mutable manifests retain normal freshness.
const TILE_CACHE = 'turku3d-immutable-v1';
const INDEX_DB = 'turku3d-asset-index';
const MAX_BYTES = 2 * 1024 ** 3, MAX_FILES = 32768, MEMORY_BYTES = 32 * 1024 ** 2;
let writes = Promise.resolve(), inventory, database, budget;
const touches = new Map(), memory = new Map(), inFlight = new Map();
let touchBatch, memoryBytes = 0;
const stats = { http: 0, storage: 0, memory: 0, joined: 0, indexMs: 0 };

function immutableAsset(request) {
  const url = new URL(request.url);
  return request.method === 'GET' && !request.headers.has('range')
    && url.pathname.startsWith('/coarse/')
    && /[./][a-f0-9]{64}\.(?:glb|ktx2|bin|col|json)$/.test(url.pathname);
}
function assetKey(url) { return new URL(url).pathname.match(/[a-f0-9]{64}\.[a-z0-9]+$/)?.[0] ?? url; }
function sharedAsset(url) { return new URL(url).pathname.startsWith('/coarse/shared/'); }
function byteBudget() {
  return budget ??= Promise.resolve().then(() => self.navigator.storage.estimate())
    .then(({ quota }) => quota > 0 ? Math.min(MAX_BYTES, Math.floor(quota / 4)) : 256 * 1024 ** 2)
    .catch(() => 256 * 1024 ** 2);
}
function openIndex() {
  return database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(INDEX_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('assets', { keyPath: 'url' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('Asset index blocked'));
  });
}
async function readIndex() {
  const db = await openIndex();
  return new Promise((resolve, reject) => {
    const request = db.transaction('assets').objectStore('assets').getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function writeIndex(rows, removed = []) {
  try {
    const db = await openIndex();
    await new Promise((resolve, reject) => {
      const tx = db.transaction('assets', 'readwrite'), store = tx.objectStore('assets');
      for (const row of rows) store.put(row);
      for (const url of removed) store.delete(url);
      tx.oncomplete = resolve; tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } catch { /* Private mode/storage denial still permits HTTP and best-effort Cache Storage. */ }
}
function entriesFor(cache) {
  return inventory ??= (async () => {
    const start = performance.now();
    let rows = await readIndex().catch(() => []);
    if (!rows.length) {
      // One-time v1 migration reads keys, never every response body. Old storage was <=192 MiB;
      // replace conservative size estimates with exact decoded sizes as those assets are used.
      const keys = await cache.keys();
      rows = keys.map(key => ({ url: key.url, bytes: Math.ceil(192 * 1024 ** 2 / keys.length), used: 0, estimated: true }));
      await writeIndex(rows);
    }
    stats.indexMs = performance.now() - start;
    return new Map(rows.sort((a, b) => a.used - b.used).map(row => [row.url, row]));
  })();
}
function touch(entries, url, response) {
  const old = entries.get(url);
  if (!old) return Promise.resolve();
  const bytes = Number(response.headers.get('X-Turku3D-Cache-Bytes'));
  const row = { url, bytes: bytes || old.bytes, used: Date.now() };
  entries.delete(url); entries.set(url, row); touches.set(url, row);
  // Keep the event alive until one small transaction flushes the whole burst. No body rewrite.
  return touchBatch ??= new Promise(resolve => setTimeout(resolve, 500)).then(async () => {
    const rows = [...touches.values()]; touches.clear(); touchBatch = undefined;
    const saved = writes = writes.catch(() => {}).then(() => writeIndex(rows.filter(row => entries.has(row.url)).map(row => entries.get(row.url))));
    await saved;
  });
}
async function trimAssets(cache, entries, maxBytes, maxFiles = MAX_FILES) {
  let bytes = [...entries.values()].reduce((sum, row) => sum + row.bytes, 0);
  const removed = [];
  for (const [url, row] of entries) {
    if (bytes <= maxBytes && entries.size <= maxFiles) break;
    await cache.delete(url); entries.delete(url); touches.delete(url); bytes -= row.bytes; removed.push(url);
  }
  if (removed.length) await writeIndex([], removed);
}
function remember(url, body, headers) {
  if (!sharedAsset(url) || body.size > MEMORY_BYTES) return;
  const key = assetKey(url), old = memory.get(key);
  if (old) memoryBytes -= old.body.size;
  memory.delete(key); memory.set(key, { body, headers }); memoryBytes += body.size;
  for (const [key, entry] of memory) {
    if (memoryBytes <= MEMORY_BYTES) break;
    memory.delete(key); memoryBytes -= entry.body.size;
  }
}
async function storeAsset(cache, request, body, headers) {
  const entries = await entriesFor(cache), maxBytes = await byteBudget();
  if (body.size > maxBytes) return;
  if (entries.has(request.url) && !entries.get(request.url).estimated) {
    const row = { ...entries.get(request.url), used: Date.now() };
    entries.delete(request.url); entries.set(request.url, row);
    await writeIndex([row]); return;
  }
  const row = { url: request.url, bytes: body.size, used: Date.now() };
  // Reserve space before put; recover from a smaller real quota without breaking streaming.
  await trimAssets(cache, entries, maxBytes - body.size, MAX_FILES - 1);
  const put = () => cache.put(request, new Response(body, { headers }));
  try { await put(); } catch {
    await trimAssets(cache, entries, Math.max(0, Math.min(maxBytes / 2, maxBytes - body.size)));
    await put();
  }
  entries.delete(request.url); entries.set(request.url, row);
  await writeIndex([row]);
}
async function readAsset(request) {
  // Immutable server responses have a one-year HTTP max-age. Start that fast path before
  // opening IndexedDB/Cache Storage; race a persistent fallback only when HTTP is slow.
  let cache, entries, httpReady = false;
  const persistent = caches.open(TILE_CACHE).then(async value => {
    cache = value; entries = await entriesFor(cache);
    if (!entries.has(request.url)) throw new Error('Not stored');
    await new Promise(resolve => setTimeout(resolve, 25));
    if (httpReady) throw new Error('HTTP cache won');
    const hit = await cache.match(request);
    if (!hit) {
      entries.delete(request.url); await writeIndex([], [request.url]);
      throw new Error('Evicted by browser');
    }
    stats.storage++;
    const saved = Promise.all([touch(entries, request.url, hit),
      sharedAsset(request.url) ? hit.clone().blob().then(body => remember(request.url, body, hit.headers)) : undefined]);
    return { response: hit, saved };
  });
  const http = fetch(request, { cache: 'force-cache' }).then(response => {
    if (response.status !== 200) throw response;
    httpReady = true; stats.http++;
    return { response };
  });
  let result;
  try { result = await Promise.any([http, persistent]); }
  catch { // Return HTTP error statuses, preserving the client's ordinary retry behavior.
    return { response: await http.catch(error => { if (error instanceof Response) return { response: error }; throw error; }).then(value => value.response) };
  }
  const response = result.response;
  if (response.headers.has('X-Turku3D-Cache-Bytes') || response.type === 'opaque'
    || !/\bimmutable\b/i.test(response.headers.get('Cache-Control') ?? '')) return result;
  const copy = response.clone();
  const saved = copy.blob().then(async body => {
    const headers = new Headers(response.headers);
    headers.delete('Content-Encoding'); headers.delete('Content-Length');
    headers.set('X-Turku3D-Cache-Bytes', String(body.size));
    remember(request.url, body, headers);
    cache ??= await caches.open(TILE_CACHE);
    const stored = writes = writes.catch(() => {}).then(() => storeAsset(cache, request, body, headers));
    await stored;
  }).catch(() => {});
  return { response, saved };
}
async function cachedAsset(request) {
  const key = assetKey(request.url), hit = memory.get(key);
  if (hit) {
    stats.memory++; memory.delete(key); memory.set(key, hit);
    const response = new Response(hit.body, { headers: hit.headers });
    const saved = caches.open(TILE_CACHE).then(async cache => touch(await entriesFor(cache), request.url, response)).catch(() => {});
    return { response, saved };
  }
  let pending = inFlight.get(key);
  if (pending) stats.joined++;
  else {
    pending = readAsset(request); inFlight.set(key, pending);
    void pending.then(value => value.saved).catch(() => {}).finally(() => inFlight.delete(key));
  }
  const result = await pending;
  return { response: result.response.clone(), saved: result.saved };
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  if (event.data?.type !== 'turku3d-cache-stats') return;
  event.waitUntil((async () => {
    await writes.catch(() => {});
    const cache = await caches.open(TILE_CACHE), entries = await entriesFor(cache);
    event.ports[0]?.postMessage({ ...stats, entries: entries.size,
      bytes: [...entries.values()].reduce((sum, row) => sum + row.bytes, 0),
      budget: await byteBudget(), memoryBytes, estimated: [...entries.values()].filter(row => row.estimated).length });
  })());
});
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
