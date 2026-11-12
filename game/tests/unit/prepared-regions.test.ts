import { expect, test, vi } from 'vitest';
import { loadPreparedRegion, readPreparedRegions, selectPreparedRegion } from '../../src/world/preparedRegions';

function fixture() {
  const spawn = { position: [2, 2, 2], headingDeg: 0, vehicle: { position: [4, 2, 4], yaw: 0 } };
  const row = { id: 'office', label: 'Riverfront', url: '/scenes/office.scene.json', release: 'a'.repeat(64), bounds: [0, 0, 10, 10], spawn };
  const held = { ...structuredClone(row), id: 'port-arthur', label: 'Port Arthur', url: '/scenes/port-arthur.scene.json', release: 'b'.repeat(64) };
  const catalog = { schema: 'turku-prepared-regions/1', defaultRegion: 'office', regions: [row, held] };
  const scene = { schema: 'turku-scene/1', ...held, origin: [23459500, 6704500, 0], sources: [], materials: [], boundaries: [], supports: [],
    meshes: [], assets: [], fixtures: [], emitters: [], lightOccluders: [],
    collision: { c_0_0: { url: 'ground.col', sha256: 'c'.repeat(64), bytes: 1, tris: 0, h: [0, 2] } },
    coverage: { mode: 'replace', bounds: held.bounds, entityIds: [] } };
  const fetcher = vi.fn(async input => Response.json(String(input).endsWith('/regions.json') ? catalog : scene)) as unknown as typeof fetch;
  return { catalog, scene, fetcher };
}

test('catalog selects its default or an explicit complete region and rejects unknown requests', () => {
  const catalog = readPreparedRegions(fixture().catalog);
  expect(selectPreparedRegion(catalog, null).id).toBe('office');
  expect(selectPreparedRegion(catalog, 'port-arthur').id).toBe('port-arthur');
  for (const request of ['', 'missing', '../office']) expect(() => selectPreparedRegion(catalog, request)).toThrow('Unknown prepared region');
});

test.each(['https://other.test/scenes/office.scene.json', '//other.test/scenes/office.scene.json', '/scenes/../office.scene.json', '/scenes/office.scene.json?source=other'])
('catalog rejects noncanonical scene URL %s', url => {
  const { catalog } = fixture(); catalog.regions[0].url = url;
  expect(() => readPreparedRegions(catalog)).toThrow('same-origin');
});

test('catalog rejects duplicate IDs, unknown fields and unsupported spawns', () => {
  const { catalog } = fixture();
  catalog.regions.push(catalog.regions[0]); expect(() => readPreparedRegions(catalog)).toThrow('duplicate');
  catalog.regions.pop();
  expect(() => readPreparedRegions({ ...catalog, fallback: 'office' })).toThrow('schema');
  catalog.regions[1].spawn.position[0] = 10; expect(() => readPreparedRegions(catalog)).toThrow('outside bounds');
});

test('loads only the requested pinned scene through the same strict reader', async () => {
  const { fetcher } = fixture();
  const result = await loadPreparedRegion('https://city.test/?region=port-arthur', fetcher);
  expect(result.data.id).toBe('port-arthur'); expect(result.region.release).toBe(result.data.release);
  expect(vi.mocked(fetcher).mock.calls.map(args => args[0])).toEqual(['https://city.test/scenes/regions.json', 'https://city.test/scenes/port-arthur.scene.json']);
});

test.each(['release', 'bounds', 'spawn', 'vehicle', 'identity'])('rejects a stale catalog %s before world allocation', async field => {
  const { scene, fetcher } = fixture();
  if (field === 'release') scene.release = 'c'.repeat(64);
  if (field === 'bounds') scene.bounds = [0, 0, 11, 10];
  if (field === 'spawn') scene.spawn = { ...scene.spawn, headingDeg: 30 };
  if (field === 'vehicle') scene.spawn = { ...scene.spawn, vehicle: { ...scene.spawn.vehicle, yaw: 1 } };
  if (field === 'identity') scene.id = 'office';
  await expect(loadPreparedRegion('https://city.test/?region=port-arthur', fetcher)).rejects.toThrow('pinned scene');
});
