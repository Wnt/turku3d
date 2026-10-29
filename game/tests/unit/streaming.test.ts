// CollisionStreamer: which tiles it requests/unloads, the tiles it loads ahead of a moving player, which tiles it tells
// the worker exist (no edge wall faces them), the gentle hold near unloaded ones, and that a city-sized manifest does
// not stall the main thread.
import { test, expect, vi } from 'vitest';
import { CollisionStreamer, holdCar, holdFoot } from '../../src/world/collisionStreaming';

function fakes(ni: number, nj: number) {
  const collision: Record<string, { url: string; bytes: number }> = {};
  for (let i = 0; i < ni; i++) for (let j = 0; j < nj; j++) collision[`c_${i}_${j}`] = { url: `c_${i}_${j}.col`, bytes: 4 };
  const manifest = { collision, baseUrl: 'http://x/' } as never;
  const loaded = new Map<string, unknown>();
  const known = new Set<string>();
  const physics = { loaded, known, loadTile: vi.fn(async (id: string) => { loaded.set(id, {}); }), unloadTile: vi.fn((id: string) => loaded.delete(id)),
    setKnown: vi.fn((add: string[], remove: string[]) => { for (const id of add) known.add(id); for (const id of remove) known.delete(id); }) };
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

test('near a tile that exists but is not loaded the player is held to a speed that stops before it', () => {
  const { manifest, physics } = fakes(4, 1);
  for (const id of ['c_0_0', 'c_1_0']) physics.loaded.set(id, {});
  const s = new CollisionStreamer(manifest, physics as never, 100);
  // 30 m before c_2_0 (x 125), moving east: stops within 28.5 m at 6 m/s².
  expect(s.speedLimit(95, 30, 40, 0)).toBeCloseTo(Math.sqrt(2 * 6 * 28.5), 5);
  expect(s.speedLimit(95, 30, -40, 0)).toBe(Infinity); // moving away
  expect(s.speedLimit(124, 30, 5, 0)).toBe(0);
  physics.loaded.set('c_2_0', {}); physics.loaded.set('c_3_0', {});
  expect(s.speedLimit(95, 30, 40, 0)).toBe(Infinity);
  // A car above the limit brakes (reversing: brakes with the throttle); a walker slows down.
  const drive = { throttle: 1, steer: 0.2, brake: 0, handbrake: false };
  expect(holdCar(drive, 20, 25)).toBe(drive);
  expect(holdCar(drive, 30, 20)).toEqual({ throttle: 0, steer: 0.2, brake: 1, handbrake: false });
  expect(holdCar(drive, 20.5, 20)).toEqual({ throttle: 0, steer: 0.2, brake: 0.3, handbrake: false });
  expect(holdCar({ ...drive, throttle: 0, brake: 1 }, -8, 2)).toEqual({ throttle: 1, steer: 0.2, brake: 0, handbrake: false });
  expect(holdFoot({ vx: 6, vz: 8, jump: false }, 5)).toEqual({ vx: 3, vz: 4, jump: false });
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
