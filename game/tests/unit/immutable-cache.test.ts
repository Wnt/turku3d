import fs from 'node:fs';
import vm from 'node:vm';
import { expect, test, vi } from 'vitest';

const source = fs.readFileSync(new URL('../../public/isolation.js', import.meta.url), 'utf8');
const asset = (hash = 'a') => `https://turku.lab.madekivi.fi/coarse/shared/${hash.repeat(64)}.ktx2`;
type Row = { url: string; bytes: number; used: number };
function worker(options: { denied?: boolean; stored?: Map<string, Response>; index?: Map<string, Row> } = {}) {
  const stored = options.stored ?? new Map<string, Response>(), index = options.index ?? new Map<string, Row>();
  const cache = {
    match: vi.fn(async (key: Request | string) => stored.get(typeof key === 'string' ? key : key.url)?.clone()),
    keys: vi.fn(async () => [...stored.keys()].map(url => new Request(url))),
    put: vi.fn(async (key: Request, response: Response) => { stored.set(key.url, response); }),
    delete: async (url: string) => stored.delete(url),
  };
  const network = vi.fn(async (_request?: Request, _options?: object) => new Response('texture', { headers: {
    'Cache-Control': 'public, max-age=31536000, immutable', 'Content-Encoding': 'br', 'Content-Length': '3',
  } }));
  const events = new Map<string, (event: unknown) => void>();
  const context = vm.createContext({ URL, Headers, Response, Request, fetch: network, performance,
    setTimeout: (callback: () => void, _ms: number) => setTimeout(callback, 0),
    caches: { open: async () => { if (options.denied) throw new Error('denied'); return cache; } },
    self: { navigator: { storage: { estimate: async () => ({ quota: 10.9 * 1024 ** 3 }) } },
      addEventListener: (name: string, callback: (event: unknown) => void) => events.set(name, callback),
      location: { origin: 'https://wnt.github.io' } },
    readRows: async () => [...index.values()],
    writeRows: async (rows: Row[], removed: string[] = []) => { rows.forEach(row => index.set(row.url, row)); removed.forEach(url => index.delete(url)); },
  });
  vm.runInContext(source, context);
  // Real IndexedDB transactions are exercised by the browser probe; keep these ordering tests tiny.
  vm.runInContext('readIndex = readRows; writeIndex = writeRows', context);
  return { cache, stored, index, network, events, context,
    immutable: vm.runInContext('immutableAsset', context) as (request: Request) => boolean,
    get: vm.runInContext('cachedAsset', context) as (request: Request) => Promise<{ response: Response; saved?: Promise<unknown> }>,
    trim: vm.runInContext('trimAssets', context) as (cache: object, entries: Map<string, Row>, bytes: number, files?: number) => Promise<void>,
  };
}

test('immutable only: manifests, ranged and unhashed requests bypass persistent storage', () => {
  const w = worker();
  expect(w.immutable(new Request(asset()))).toBe(true);
  for (const url of ['https://turku.lab.madekivi.fi/coarse/t0_0_0/manifest.json',
    'https://turku.lab.madekivi.fi/coarse/t0_0_0/collision.json', asset().replace('/coarse/', '/other/')]) {
    expect(w.immutable(new Request(url))).toBe(false);
  }
  expect(w.immutable(new Request(asset(), { headers: { Range: 'bytes=0-3' } }))).toBe(false);
});

test('cold worker reads the size index without matching bodies; HTTP cache wins before Cache Storage', async () => {
  const w = worker();
  const cold = await w.get(new Request(asset())); await cold.saved;
  expect(w.index.get(asset())?.bytes).toBe(7);
  const fresh = worker({ stored: w.stored, index: w.index });
  const warm = await fresh.get(new Request(asset()));
  expect(await warm.response.text()).toBe('texture'); await warm.saved;
  expect(fresh.network).toHaveBeenCalledWith(expect.any(Request), { cache: 'force-cache' });
  expect(fresh.cache.keys).not.toHaveBeenCalled();
  expect(fresh.cache.match).not.toHaveBeenCalled();
  await expect(vm.runInContext('byteBudget()', fresh.context)).resolves.toBe(2 * 1024 ** 3);
});

test('a restarted offline worker reuses stored bytes and touches LRU without rewriting bodies', async () => {
  const w = worker(); await (await w.get(new Request(asset()))).saved;
  const fresh = worker({ stored: w.stored, index: w.index });
  fresh.network.mockRejectedValue(new Error('offline'));
  const hit = await fresh.get(new Request(asset()));
  expect(await hit.response.text()).toBe('texture'); await hit.saved;
  expect(hit.response.headers.get('Content-Encoding')).toBeNull();
  expect(hit.response.headers.get('X-Turku3D-Cache-Bytes')).toBe('7');
  expect(fresh.cache.put).not.toHaveBeenCalled();
  expect(fresh.index.get(asset())!.used).toBeGreaterThan(0);
});

test('in-flight and memory hits share one fetch across page/worker consumers', async () => {
  const w = worker();
  const results = await Promise.all(Array.from({ length: 22 }, () => w.get(new Request(asset()))));
  expect(await Promise.all(results.map(value => value.response.text()))).toEqual(Array(22).fill('texture'));
  await results[0].saved;
  expect(await (await w.get(new Request(asset()))).response.text()).toBe('texture');
  expect(w.network).toHaveBeenCalledTimes(1);
});

test('storage denial, mutable responses and HTTP failures preserve fetching and retry', async () => {
  const denied = worker({ denied: true });
  expect(await (await denied.get(new Request(asset()))).response.text()).toBe('texture');
  const w = worker();
  w.network.mockResolvedValueOnce(new Response('retry', { status: 503 }));
  expect((await w.get(new Request(asset()))).response.status).toBe(503);
  await new Promise(resolve => setTimeout(resolve, 5));
  w.network.mockResolvedValueOnce(new Response('mutable'));
  await (await w.get(new Request(asset()))).saved;
  expect(w.stored.size).toBe(0);
});

test('LRU hit survives byte/count eviction and persists across another worker restart', async () => {
  const w = worker();
  for (const hash of ['a', 'b', 'c']) await (await w.get(new Request(asset(hash)))).saved;
  const fresh = worker({ stored: w.stored, index: w.index });
  fresh.network.mockRejectedValue(new Error('offline'));
  await (await fresh.get(new Request(asset()))).saved;
  const entries = await vm.runInContext('inventory', fresh.context) as Map<string, Row>;
  await fresh.trim(fresh.cache, entries, 14, 3);
  expect([...entries.keys()]).toEqual([asset('c'), asset()]);
  await fresh.trim(fresh.cache, entries, 14, 1);
  expect([...fresh.stored.keys()]).toEqual([asset()]);
  expect([...fresh.index.keys()]).toEqual([asset()]);
});

test('scenery preparation descriptors remain mutable while completed geometry is immutable', () => {
  const w = worker();
  const key = 'a'.repeat(64);
  expect(w.immutable(new Request(`https://draft.test/coarse/scenery/${key}.json`))).toBe(false);
  expect(w.immutable(new Request(`https://draft.test/coarse/scenery/lod.${key}.glb`))).toBe(true);
});
