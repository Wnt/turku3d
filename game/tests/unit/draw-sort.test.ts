import { expect, test } from 'vitest';
import { materialBandSort } from '../../src/render/drawSort';

test('draws group by material within 25 m depth bands; bands stay front to back; render order still rules', () => {
  let id = 0;
  const item = (z: number, material: number, renderOrder = 0) => ({ groupOrder: 0, renderOrder, z, id: id++, material: { id: material }, geometry: { id: id } });
  const items = [item(3, 2), item(5, 1), item(8, 2), item(12, 1), item(30, 1), item(28, 2), item(1, 3, 1)];
  const sorted = [...items].sort(materialBandSort());
  expect(sorted.map(i => [i.z, i.material.id])).toEqual([[5, 1], [12, 1], [3, 2], [8, 2], [30, 1], [28, 2], [1, 3]]);
  // Material switches: 2 in the first band, 1 in the second, instead of every draw.
  const switches = (list: typeof items) => list.slice(1).filter((x, i) => x.material.id !== list[i].material.id).length;
  expect(switches(sorted)).toBeLessThan(switches([...items].sort((a, b) => a.z - b.z)));
});
