import fs from 'node:fs';
import vm from 'node:vm';
import { expect, test, vi } from 'vitest';

const source = fs.readFileSync(new URL('../../public/isolation.js', import.meta.url), 'utf8');
const asset = (hash = 'a') => `https://turku.lab.madekivi.fi/coarse/shared/${hash.repeat(64)}.ktx2`;

function worker(denied = false) {
  const stored = new Map<string, Response>();
  const cache = {
    match: async (key: Request | string) => stored.get(typeof key === 'string' ? key : key.url)?.clone(),
    keys: async () => [...stored.keys()].map(url => new Request(url)),
    put: async (key: Request, response: Response) => { stored.set(key.url, response); },
    delete: async (url: string) => stored.delete(url),
  };
  const network = vi.fn(async () => new Response('texture', { headers: {
    'Cache-Control': 'public, max-age=31536000, immutable', 'Content-Encoding': 'br', 'Content-Length': '3',
  } }));
  const events = new Map<string, (event: unknown) => void>();
  const context = vm.createContext({ URL, Headers, Response, Request, fetch: network,
    caches: { open: async () => { if (denied) throw new Error('denied'); return cache; } },
    self: { addEventListener: (name: string, callback: (event: unknown) => void) => events.set(name, callback),
      location: { origin: 'https://wnt.github.io' } },
  });
  vm.runInContext(source, context);
  return { cache, stored, network, events,
    immutable: vm.runInContext('immutableAsset', context) as (request: Request) => boolean,
    get: vm.runInContext('cachedAsset', context) as (request: Request) => Promise<{ response: Response; saved?: Promise<void> }>,
    trim: vm.runInContext('trimAssets', context) as (cache: object, entries: Map<string, number>, bytes: number, files: number) => Promise<void>,
  };
}

test('immutable world bytes survive worker restart; mutable, ranged and unhashed files bypass storage', async () => {
  const w = worker();
  expect(w.immutable(new Request(asset()))).toBe(true);
  for (const url of ['https://turku.lab.madekivi.fi/coarse/t0_0_0/manifest.json',
    'https://turku.lab.madekivi.fi/coarse/t0_0_0/collision.json', asset().replace('/coarse/', '/other/')]) {
    expect(w.immutable(new Request(url))).toBe(false);
  }
  expect(w.immutable(new Request(asset(), { headers: { Range: 'bytes=0-3' } }))).toBe(false);
  const cold = await w.get(new Request(asset()));
  expect(await cold.response.text()).toBe('texture'); await cold.saved;
  // A fresh worker uses CacheStorage, not an in-memory response or a second network request.
  const fresh = worker(); fresh.stored.set(asset(), w.stored.get(asset())!);
  const warm = await fresh.get(new Request(asset()));
  expect(await warm.response.text()).toBe('texture'); expect(fresh.network).not.toHaveBeenCalled();
  expect(warm.response.headers.get('Content-Encoding')).toBeNull();
  expect(warm.response.headers.get('X-Turku3D-Cache-Bytes')).toBe('7');
  await (await fresh.get(new Request(asset('b')))).saved;
  expect(fresh.network).toHaveBeenCalledTimes(1);
});

test('storage denial, nonimmutable responses and HTTP failures preserve ordinary fetching', async () => {
  const denied = worker(true);
  expect(await (await denied.get(new Request(asset()))).response.text()).toBe('texture');
  const w = worker();
  w.network.mockResolvedValueOnce(new Response('retry', { status: 503 }));
  expect((await w.get(new Request(asset()))).response.status).toBe(503);
  w.network.mockResolvedValueOnce(new Response('mutable'));
  await (await w.get(new Request(asset()))).saved;
  expect(w.stored.size).toBe(0);
});

test('stored decoded bytes and file count are both bounded, evicting the oldest assets', async () => {
  const w = worker();
  for (const hash of ['a', 'b', 'c']) await (await w.get(new Request(asset(hash)))).saved;
  const entries = new Map([...w.stored.keys()].map(url => [url, 7]));
  await w.trim(w.cache, entries, 14, 3);
  expect([...entries.keys()]).toEqual([asset('b'), asset('c')]);
  await w.trim(w.cache, entries, 14, 1);
  expect([...w.stored.keys()]).toEqual([asset('c')]);
});
