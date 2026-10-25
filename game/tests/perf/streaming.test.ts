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

test('a city-sized manifest (84k collision tiles) updates in well under 8 ms', () => {
  const { manifest, physics } = fakes(300, 280);
  const s = new CollisionStreamer(manifest, physics as never, 200, 50);
  s.update(5000, 5000, true);
  const t0 = performance.now();
  for (let k = 0; k < 20; k++) s.update(5000 + k, 5000, true);
  expect((performance.now() - t0) / 20).toBeLessThan(2);
});
