import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateBuildings } from '../src/buildings.ts';

function fixture(open = false) {
  const p = [0, 0, 0, 10, 0, 0, 10, 0, 10, 0, 0, 10,
    0, 8, 0, 10, 8, 0, 10, 8, 10, 0, 8, 10];
  const i = [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6,
    0, 4, 5, 0, 5, 1, 1, 5, 6, 1, 6, 2,
    2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0];
  return { tiles: [{ positions: p, indices: open ? i.slice(0, 12) : i, building_index: Array(open ? 4 : 12).fill(0) }],
    building_ids: ['test'],
    base_heights: { test: 0 }, footprints: { type: 'FeatureCollection', features: [
      { type: 'Feature', id: 'test', geometry: { type: 'Polygon', coordinates: [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
      ] } },
    ] } };
}

test('buildings layer blocks walkers; missing walls fail the same validator', async () => {
  const closed = await validateBuildings(fixture(), 8);
  assert.equal(closed.pass, true);
  assert.equal(closed.entered, 0);
  assert.equal(closed.walkers, 8);
  const open = await validateBuildings(fixture(true), 8);
  assert.equal(open.pass, false);
  assert.ok(open.entered > 0);
});


test('nested parts are tested independently and cannot hide behind enclosing walls', async () => {
  const data = fixture();
  const inner = fixture(true);
  const points = inner.tiles[0].positions.map((v, i) => i % 3 === 1 ? v : 3 + v * .4);
  data.tiles.push({ positions: points, indices: inner.tiles[0].indices, building_index: Array(4).fill(1) });
  data.building_ids.push('inner');
  Object.assign(data.base_heights, { inner: 0 });
  data.footprints.features.push({ type: 'Feature', id: 'inner', geometry: { type: 'Polygon',
    coordinates: [[[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]] } });
  const broken = await validateBuildings(data, 16);
  assert.equal(broken.pass, false);
  assert.ok(broken.perBuilding.inner > 0);
  assert.ok(broken.failures.some(f => f.building === 'inner'));
  data.tiles[1].indices = fixture().tiles[0].indices;
  data.tiles[1].building_index = Array(12).fill(1);
  const closed = await validateBuildings(data, 16);
  assert.equal(closed.pass, true);
  assert.deepEqual(closed.untestedBuildings, []);
});
