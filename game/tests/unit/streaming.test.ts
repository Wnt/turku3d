// CollisionStreamer: which tiles it requests/unloads, the tiles it loads ahead of a moving player, which tiles it tells
// the worker exist (no edge wall faces them), active collision load lifetimes, and that a city-sized manifest does
// not stall the main thread.
import { test, expect, vi } from 'vitest';
import { CollisionStreamer } from '../../src/world/collisionStreaming';

function fakes(ni: number, nj: number) {
  const collision: Record<string, { url: string; bytes: number }> = {};
  for (let i = 0; i < ni; i++) for (let j = 0; j < nj; j++) collision[`c_${i}_${j}`] = { url: `c_${i}_${j}.col`, bytes: 4 };
  const manifest = { collision, baseUrl: 'http://x/' } as never;
  const loaded = new Map<string, unknown>();
  const known = new Set<string>();
  const physics = { loaded, known, loadTile: vi.fn(async (id: string, _buffer: ArrayBuffer) => { loaded.set(id, {}); }), unloadTile: vi.fn((id: string) => loaded.delete(id)),
    setTileLoading: vi.fn(), setKnown: vi.fn((add: string[], remove: string[]) => { for (const id of add) known.add(id); for (const id of remove) known.delete(id); }) };
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })));
  return { manifest, physics };
}

test('requests the nearest tiles first (max 4 in flight), unloads beyond radius + hysteresis', async () => {
  const { manifest, physics } = fakes(16, 16);
  const s = new CollisionStreamer(manifest, physics as never, 100, 50);
  const started = s.update(300, 300, true); // inside c_4_4
  expect(started[0]).toBe('c_4_4');
  expect(started.length).toBe(4);
  for (let k = 0; k < 30 && s.missingNear(300, 300, 100).length; k++) { await new Promise((r) => setTimeout(r, 0)); s.update(300, 300, true); }
  expect(s.missingNear(300, 300, 100)).toEqual([]);
  const n = physics.loaded.size;
  expect(n).toBeGreaterThanOrEqual(9);
  s.update(900, 900, true); // far away: everything previously loaded goes
  for (const id of ['c_4_4', 'c_3_3']) expect(physics.loaded.has(id)).toBe(false);
  expect(physics.unloadTile).toHaveBeenCalledTimes(n);
});

test('a city-sized manifest (84k collision tiles) updates without changing its bounded request capacity', () => {
  const { manifest, physics } = fakes(300, 280);
  const s = new CollisionStreamer(manifest, physics as never, 200, 50);
  s.update(5000, 5000, true);
  for (let k = 0; k < 20; k++) s.update(5000 + k, 5000, true);
  expect(s.pending).toBe(4);
});

test('full request capacity still unloads distant tiles and resumes after requests finish', async () => {
  const { manifest, physics } = fakes(16, 16);
  const s = new CollisionStreamer(manifest, physics as never, 100, 50);
  expect(s.update(300, 300, true)).toHaveLength(4);
  physics.loaded.set('c_0_0', {});
  expect(s.update(900, 900, true)).toEqual([]);
  expect(physics.unloadTile).toHaveBeenCalledWith('c_0_0');
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(s.pending).toBe(0);
  expect(s.update(900, 900, true)).toHaveLength(4);
});

test.each([404, 503])('network/HTTP %s failures retry with exponential backoff and clear after success', async status => {
  const { manifest, physics } = fakes(1, 1);
  const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const fetcher = vi.mocked(fetch).mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce({ ok: false, status } as Response)
    .mockResolvedValue({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) } as Response);
  const s = new CollisionStreamer(manifest, physics as never, 10);
  try {
    expect(s.update(20, 20, true)).toEqual(['c_0_0']);
    await new Promise(r => setTimeout(r, 0));
    clock.mockReturnValue(499); expect(s.update(20,20,true)).toEqual([]);
    clock.mockReturnValue(500); expect(s.update(20,20,true)).toEqual(['c_0_0']);
    await new Promise(r => setTimeout(r, 0));
    clock.mockReturnValue(1499); expect(s.update(20,20,true)).toEqual([]);
    clock.mockReturnValue(1500); expect(s.update(20,20,true)).toEqual(['c_0_0']);
    await new Promise(r => setTimeout(r, 0));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(physics.loaded.has('c_0_0')).toBe(true);
  } finally { clock.mockRestore(); warn.mockRestore(); }
});

test('a failed collision upgrade retries while the previous collider remains loaded', async () => {
  const { manifest, physics } = fakes(1, 1);
  const entries = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision;
  entries.c_0_0.sha256 = 'old';
  const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const s = new CollisionStreamer(manifest, physics as never, 10);
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  try {
    s.retain('c_0_0', entries.c_0_0, new ArrayBuffer(4));
    s.update(20, 20, true); await tick();
    entries.c_0_0 = { ...entries.c_0_0, sha256: 'latest', url: 'latest.col' };
    vi.mocked(fetch).mockRejectedValueOnce(new Error('temporary upgrade failure'));
    s.refresh(['c_0_0']); await tick();
    expect(physics.loaded.has('c_0_0')).toBe(true);
    expect(physics.unloadTile).not.toHaveBeenCalled();
    clock.mockReturnValue(499); expect(s.update(20, 20, true)).toEqual([]);
    clock.mockReturnValue(500); expect(s.update(20, 20, true)).toEqual(['c_0_0']);
    await tick();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(physics.loadTile).toHaveBeenCalledTimes(2);
    expect(s.pending).toBe(0);
    s.refresh(['c_0_0']);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(physics.unloadTile).not.toHaveBeenCalled();
  } finally { clock.mockRestore(); warn.mockRestore(); }
});

for (const failure of ['size', 'CRC'] as const) test(`${failure} failures are permanent and surfaced`, async () => {
  const { manifest, physics } = fakes(1,1);
  const onError = vi.fn(); Object.assign(physics, { onError });
  if (failure === 'size') vi.mocked(fetch).mockResolvedValue({ ok:true, arrayBuffer:async()=>new ArrayBuffer(3) } as Response);
  else physics.loadTile.mockRejectedValue(Object.assign(new Error('CRC-32 mismatch'), { permanent:true }));
  const s = new CollisionStreamer(manifest, physics as never,10);
  s.update(20,20,true);
  await new Promise(r=>setTimeout(r,0));
  expect(s.update(20,20,true)).toEqual([]);
  expect(onError).toHaveBeenCalledWith(expect.stringContaining(failure === 'size' ? 'size' : 'CRC'));
  expect(s.pending).toBe(0);
});

const settle = async (s: CollisionStreamer, x: number, z: number, vx = 0, vz = 0) => {
  for (let k = 0; k < 60 && (s.pending || s.update(x, z, true, vx, vz).length); k++) await new Promise(r => setTimeout(r, 0));
};

test.each([false, true])('an obsolete HTTP request cannot install or fail replacement collision (failed=%s)', async fail => {
  const { manifest, physics } = fakes(1, 1);
  const entries = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision;
  entries.c_0_0.sha256 = 'old';
  let finish!: (response: Response) => void, reject!: (error: Error) => void;
  vi.mocked(fetch).mockReturnValueOnce(new Promise<Response>((yes, no) => { finish = yes; reject = no; }));
  const s = new CollisionStreamer(manifest, physics as never, 10);
  s.update(20, 20, true);
  entries.c_0_0 = { ...entries.c_0_0, sha256: 'latest', url: 'latest.col' };
  const bytes = Uint8Array.of(2, 3, 4, 5).buffer;
  s.retain('c_0_0', entries.c_0_0, bytes);
  s.refresh(['c_0_0']);
  if (fail) reject(Object.assign(new Error('obsolete failure'), { permanent: true }));
  else finish(new Response(new Uint8Array(4)));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(physics.loadTile).toHaveBeenCalledTimes(1);
  expect(physics.loadTile).toHaveBeenCalledWith('c_0_0', bytes);
  expect(s.progressAt(20, 20)).toEqual({ loaded: 1, total: 1, errors: [] });
  expect(s.pending).toBe(0);
  expect(physics.setTileLoading.mock.calls).toEqual([['c_0_0', true], ['c_0_0', false]]);
});

test.each([false, true])('worker acknowledgement follows a changed or removed manifest without orphan colliders (removed=%s)', async removed => {
  const { manifest, physics } = fakes(1, 1);
  const entries = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision;
  entries.c_0_0.sha256 = 'old';
  const s = new CollisionStreamer(manifest, physics as never, 10);
  let acknowledge!: () => void;
  physics.loadTile.mockImplementationOnce(async id => { await new Promise<void>(yes => { acknowledge = yes; }); physics.loaded.set(id, {}); });
  s.retain('c_0_0', entries.c_0_0, new ArrayBuffer(4));
  s.update(20, 20, true);
  expect(s.pending).toBe(1);
  const bytes = Uint8Array.of(2, 3, 4, 5).buffer;
  if (removed) delete entries.c_0_0;
  else {
    entries.c_0_0 = { ...entries.c_0_0, sha256: 'latest', url: 'latest.col' };
    s.retain('c_0_0', entries.c_0_0, bytes);
    s.refresh(['c_0_0']);
  }
  acknowledge();
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(fetch).not.toHaveBeenCalled();
  expect(s.pending).toBe(0);
  expect(physics.loaded.has('c_0_0')).toBe(!removed);
  if (removed) expect(physics.unloadTile).toHaveBeenCalledWith('c_0_0');
  else {
    expect(physics.loadTile).toHaveBeenCalledTimes(2);
    expect(physics.loadTile).toHaveBeenLastCalledWith('c_0_0', bytes);
    s.refresh(['c_0_0']);
    expect(physics.loadTile).toHaveBeenCalledTimes(2);
  }
  expect(physics.setTileLoading.mock.calls).toEqual([['c_0_0', true], ['c_0_0', false]]);
});

test('a moving player gets the collision along the next 8 s of travel loaded, and keeps it beyond the radius', async () => {
  const { manifest, physics } = fakes(40, 4);
  const s = new CollisionStreamer(manifest, physics as never, 100, 50);
  // 40 m/s east along z 100 from x 100: radius 100 plus 320 m ahead.
  await settle(s, 100, 100, 40, 0);
  for (const id of ['c_3_1', 'c_6_1', 'c_8_1', 'c_8_2']) expect(physics.loaded.has(id)).toBe(true);
  expect(physics.loaded.has('c_10_1')).toBe(false); // 625 m: beyond 100 + 100 + 320 and the one-tile corridor
  // Tiles behind the player stay within the radius only.
  expect(physics.loaded.has('c_0_1')).toBe(true);
  // Standing, the ahead tiles beyond radius + hysteresis unload again; ahead of a walker nothing extra loads.
  s.update(100, 100, true);
  expect(physics.loaded.has('c_8_1')).toBe(false);
  expect(s.ahead(100, 100, 2, 0)).toEqual([]);
});

test('the worker learns the neighbours of loaded tiles that exist; a tile that failed for good is not one', async () => {
  const { manifest, physics } = fakes(4, 4);
  physics.loadTile.mockImplementation(async id => {
    if (id === 'c_1_0') throw Object.assign(new Error('CRC mismatch'), { permanent: true });
    physics.loaded.set(id, {});
  });
  const s = new CollisionStreamer(manifest, physics as never, 40);
  Object.assign(physics, { onError: () => {} });
  await settle(s, 30, 10);
  expect([...physics.loaded.keys()]).toEqual(['c_0_0']);
  // c_0_1 exists and is not loaded: no wall faces it. c_1_0 failed: a wall does. c_-1_0 and c_0_-1 do not exist.
  expect([...physics.known].sort()).toEqual(['c_0_0', 'c_0_1']);
  physics.unloadTile('c_0_0');
  s.update(500, 500, true);
  expect([...physics.known]).toEqual([]);
});

test('preloads the union of distant starts and does not evict either while the menu focus changes', async () => {
  const { manifest, physics } = fakes(20, 2);
  const s = new CollisionStreamer(manifest, physics as never, 40, 20);
  s.retainedStarts = [{ x: 30, z: 30 }, { x: 1000, z: 30 }];
  await settle(s, 30, 30);
  expect(s.progressAt(30, 30).loaded).toBe(s.progressAt(30, 30).total);
  expect(physics.loaded.has('c_16_0')).toBe(true);
  s.update(1000, 30, true);
  expect(physics.loaded.has('c_0_0')).toBe(true);
  s.retainedStarts = [];
  s.update(1000, 30, true);
  expect(physics.loaded.has('c_0_0')).toBe(false);
});

test('startup coverage expands into the normal streaming ring without dropping spawn support', async () => {
  const { manifest, physics } = fakes(16, 16);
  const s = new CollisionStreamer(manifest, physics as never, 80);
  for (let k = 0; k < 20; k++) { s.update(300, 300, true); await new Promise(r => setTimeout(r, 0)); }
  const startCells = [...physics.loaded.keys()];
  expect(s.supports(300, 300, 3)).toBe(true);
  expect(s.progressAt(300, 300).loaded).toBe(s.progressAt(300, 300).total);
  const shortCount = startCells.length;
  s.radius = 200;
  expect(s.progressAt(300, 300).total).toBeGreaterThan(shortCount * 2);
  for (let k = 0; k < 30; k++) { s.update(300, 300, true); await new Promise(r => setTimeout(r, 0)); }
  expect(s.progressAt(300, 300).loaded).toBe(s.progressAt(300, 300).total);
  for (const id of startCells) expect(physics.loaded.has(id)).toBe(true);
});

test('validated bytes reach Rapier without refetch, but stale versions cannot be reused', async () => {
  const { manifest, physics } = fakes(1, 1);
  const s = new CollisionStreamer(manifest, physics as never, 10);
  const entry = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision.c_0_0;
  const buffer = new ArrayBuffer(4);
  s.retain('c_0_0', entry, buffer);
  s.update(20, 20, true);
  await new Promise(r => setTimeout(r, 0));
  expect(fetch).not.toHaveBeenCalled();
  expect(physics.loadTile).toHaveBeenCalledWith('c_0_0', buffer);
  expect(physics.loadTile.mock.calls[0][1]).not.toBe(buffer);
  s.retain('c_0_0', { ...entry, sha256: 'old' }, new ArrayBuffer(4));
  s.refresh(['c_0_0']);
  await new Promise(r => setTimeout(r, 0));
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('resident collision bytes survive worker transfer and range revisits; unchanged refresh does not rebuild', async () => {
  const { manifest, physics } = fakes(1, 1);
  const entries = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision;
  entries.c_0_0.sha256 = 'one';
  const s = new CollisionStreamer(manifest, physics as never, 10);
  const bytes = Uint8Array.of(1, 2, 3, 4).buffer;
  s.retain('c_0_0', entries.c_0_0, bytes);
  physics.loadTile.mockImplementation(async (id: string, buffer: ArrayBuffer) => {
    expect([...new Uint8Array(buffer)]).toEqual([1, 2, 3, 4]);
    structuredClone(buffer, { transfer: [buffer] });
    physics.loaded.set(id, {});
  });
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  s.update(20, 20, true); await tick();
  s.refresh(['c_0_0']); await tick();
  expect(physics.loadTile).toHaveBeenCalledTimes(1);
  // A new serving generation can move an unchanged payload without changing its content identity.
  entries.c_0_0 = { ...entries.c_0_0, url: '/coarse/_v/new-generation/c_0_0.col' };
  s.update(20, 20, true);
  s.refresh(['c_0_0']); await tick();
  expect(physics.loadTile).toHaveBeenCalledTimes(1);
  s.update(1000, 1000, true);
  expect(physics.loaded.has('c_0_0')).toBe(false);
  s.update(20, 20, true); await tick();
  expect(physics.loadTile).toHaveBeenCalledTimes(2);
  expect(bytes.byteLength).toBe(4);
  expect(fetch).not.toHaveBeenCalled();
  entries.c_0_0 = { ...entries.c_0_0, sha256: 'two', url: 'new.col' };
  vi.mocked(fetch).mockResolvedValue({ ok: true, arrayBuffer: async () => Uint8Array.of(1, 2, 3, 4).buffer } as Response);
  s.refresh(['c_0_0']); await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(physics.loadTile).toHaveBeenCalledTimes(3);
});


test('only active known collision requests permit a worker hold; failures clear loading before retry', async () => {
  const { manifest, physics } = fakes(1, 1);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  let reject!: (error: Error) => void;
  vi.mocked(fetch).mockReturnValueOnce(new Promise((_, no) => { reject = no; }));
  const s = new CollisionStreamer(manifest, physics as never, 10);
  try {
    s.update(20, 20, true);
    expect(physics.setTileLoading.mock.calls).toEqual([['c_0_0', true]]);
    reject(new Error('offline'));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(physics.setTileLoading.mock.calls).toEqual([['c_0_0', true], ['c_0_0', false]]);
    expect(s.pending).toBe(0);
  } finally { warn.mockRestore(); }
});

test('new retained collision wakes admission before the 250 ms polling interval', async () => {
  const { manifest, physics } = fakes(0, 0);
  const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
  const s = new CollisionStreamer(manifest, physics as never, 100);
  try {
    expect(s.update(20, 20)).toEqual([]);
    const entry = { url: 'c_0_0.col', bytes: 4, sha256: 'ready', tris: 1, h: [0, 1] as [number, number] };
    (manifest as { collision: Record<string, typeof entry> }).collision.c_0_0 = entry;
    s.retain('c_0_0', entry, new ArrayBuffer(4));
    clock.mockReturnValue(16);
    expect(s.update(20, 20)).toEqual(['c_0_0']);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(s.supports(20, 20)).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  } finally { clock.mockRestore(); }
});

test('completed worker loads refill the bounded four slots on the next frame', async () => {
  const { manifest, physics } = fakes(16, 16);
  const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
  const s = new CollisionStreamer(manifest, physics as never, 100);
  try {
    expect(s.update(300, 300)).toHaveLength(4);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(s.pending).toBe(0);
    clock.mockReturnValue(16);
    expect(s.update(300, 300)).toHaveLength(4);
    expect(s.pending).toBe(4);
    await new Promise(resolve => setTimeout(resolve, 0));
  } finally { clock.mockRestore(); }
});

test('collision replacements share four slots and drain without waiting for another update', async () => {
  const { manifest, physics } = fakes(6, 1);
  const s = new CollisionStreamer(manifest, physics as never, 1000);
  const entries = (manifest as { collision: Record<string, import('../../src/world/manifest').ManifestEntry> }).collision;
  const releases: (() => void)[] = [];
  let active = 0, peak = 0;
  physics.loadTile.mockImplementation(async id => {
    peak = Math.max(peak, ++active);
    await new Promise<void>(resolve => releases.push(resolve));
    active--; physics.loaded.set(id, {});
  });
  for (const [id, entry] of Object.entries(entries)) {
    physics.loaded.set(id, {});
    s.retain(id, entry, new ArrayBuffer(4));
  }
  // An ordinary load is already using one slot when the other five cells upgrade.
  physics.loaded.delete('c_0_0');
  s.update(20, 20, true);
  s.refresh(Object.keys(entries));
  expect(physics.loadTile).toHaveBeenCalledTimes(4);
  expect(s.pending).toBe(6);
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  releases.shift()!(); await tick();
  expect(physics.loadTile).toHaveBeenCalledTimes(5);
  expect(active).toBe(4);
  releases.shift()!(); await tick();
  expect(physics.loadTile).toHaveBeenCalledTimes(6);
  while (releases.length) { releases.shift()!(); await tick(); }
  expect(peak).toBe(4);
  expect(s.pending).toBe(0);
  expect(fetch).not.toHaveBeenCalled();
  expect(physics.unloadTile).not.toHaveBeenCalled();
});
