// CollisionStreamer: which tiles it requests/unloads, and that a city-sized manifest does not stall the main thread.
import { test, expect, vi } from 'vitest';
import { CollisionStreamer } from '../../src/world/collisionStreaming';

function fakes(ni: number, nj: number) {
  const collision: Record<string, { url: string; bytes: number }> = {};
  for (let i = 0; i < ni; i++) for (let j = 0; j < nj; j++) collision[`c_${i}_${j}`] = { url: `c_${i}_${j}.col`, bytes: 4 };
  const manifest = { collision, baseUrl: 'http://x/' } as never;
  const loaded = new Map<string, unknown>();
  const physics = { loaded, loadTile: vi.fn(async (id: string) => { loaded.set(id, {}); }), unloadTile: vi.fn((id: string) => loaded.delete(id)) };
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

test('a city-sized manifest (84k collision tiles) updates in well under 8 ms', () => {
  const { manifest, physics } = fakes(300, 280);
  const s = new CollisionStreamer(manifest, physics as never, 200, 50);
  s.update(5000, 5000, true);
  const t0 = performance.now();
  for (let k = 0; k < 20; k++) s.update(5000 + k, 5000, true);
  expect((performance.now() - t0) / 20).toBeLessThan(2);
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

test('network/503 failures retry with exponential backoff and clear after success', async () => {
  const { manifest, physics } = fakes(1, 1);
  const clock = vi.spyOn(performance, 'now').mockReturnValue(0);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const fetcher = vi.mocked(fetch).mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce({ ok: false, status: 503 } as Response)
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
