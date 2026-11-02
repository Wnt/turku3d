import { expect, test, vi } from 'vitest';
import { createGameplay } from '../../src/gameplay';
import type { Manifest } from '../../src/world/manifest';
import type { RoadGraphJson } from '../../src/gameplay/roadGraph';

const minimap = vi.hoisted(() => ({ setGraph: vi.fn(), setArea: vi.fn() }));
vi.mock('../../src/gameplay/minimap', () => ({ Minimap: class {
  setGraph = minimap.setGraph;
  setArea = minimap.setArea;
} }));

test('a failed published road upgrade retries its hash and keeps the current graph until it succeeds', async () => {
  const roads: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    bbox: [0, 0, 10, 10], nodes: [0, 0, 10, 0], edges: [[0, 1, 3, 10]] };
  const manifest = { visual: {}, collision: {}, baseUrl: 'http://localhost/tiles/',
    roads: { url: 'roads.old.json', sha256: 'old', bytes: 100 } } as Manifest;
  let newRequests = 0;
  const fetcher = vi.fn(async (url: string | URL | Request) => {
    const href = String(url);
    if (href.endsWith('/navigation.json')) return new Response('', { status: 404 });
    if (href.endsWith('roads.new.json') && ++newRequests === 1) return new Response('', { status: 503 });
    return Response.json(roads);
  });
  vi.stubGlobal('location', new URL('http://localhost/'));
  vi.stubGlobal('fetch', fetcher);
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    const gameplay = await createGameplay(manifest, {} as HTMLElement);
    manifest.roads = { ...manifest.roads!, url: 'roads.new.json', sha256: 'new' };
    await gameplay.streets.update([]);
    expect(newRequests).toBe(1);
    expect(gameplay.streets.updates).toBe(0);
    expect(minimap.setGraph).not.toHaveBeenCalled();
    await gameplay.streets.update([]);
    expect(newRequests).toBe(2);
    expect(gameplay.streets.updates).toBe(1);
    expect(minimap.setGraph).toHaveBeenCalledTimes(1);
    const draft: RoadGraphJson = { ...roads, bbox: [20, 0, 30, 10], nodes: [20, 0, 30, 0] };
    await gameplay.streets.update([draft]);
    expect(gameplay.streets.updates).toBe(2);
    expect(minimap.setGraph.mock.lastCall![0].edges).toHaveLength(2);
    await gameplay.streets.update([draft]);
    expect(gameplay.streets.updates).toBe(2);
    await gameplay.streets.update([]);
    expect(gameplay.streets.updates).toBe(3);
    expect(minimap.setGraph.mock.lastCall![0].edges).toHaveLength(1);
    // Once installed, repeated map updates reuse the successfully loaded payload.
    await gameplay.streets.update([]);
    expect(newRequests).toBe(2);
    expect(gameplay.streets.updates).toBe(3);
    expect(minimap.setGraph).toHaveBeenCalledTimes(3);
  } finally { warning.mockRestore(); vi.unstubAllGlobals(); }
});
