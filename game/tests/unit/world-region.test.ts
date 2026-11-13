import { expect, test } from 'vitest';
import { chunkDistance, readChunk, readRegion } from '../../src/world/location/region';

const url = (c: string) => `objects/${c.repeat(2)}/${c.repeat(64)}.json`;
const index = {
  schema: 'turku-region/1', name: 'centre', revision: 3, built: '2026-11-13T18:00:00Z', origin: [23459500, 6704500, 0],
  centre: [23459700, 6704580], focus: [200, 3, -80], chunk: 250, unit: 500,
  chunks: { '0_-1': { url: url('a'), sha256: 'a'.repeat(64), bytes: 10, entities: 1, triangles: 12, y: [0, 20] } },
  collision: {}, roads: {}, units: [], stats: { entities: 1, triangles: 12, bytes: 100, instances: 0, byKind: {} },
};

test('a region index is read with its chunks, and a chunk object only as the chunk it is listed as', () => {
  const region = readRegion(index);
  const row = { id: 'building/a', kind: 'building', tier: 'auto', role: 'scene', hash: 'h', origin: [10, 0, -10], bounds: [0, 0, 0, 1, 1, 1], instances: [] };
  expect(readChunk(region, '0_-1', { schema: 'turku-chunk/1', chunk: [0, -1], entities: [row] })).toHaveLength(1);
  expect(() => readChunk(region, '0_-1', { schema: 'turku-chunk/1', chunk: [1, -1], entities: [row] })).toThrow(/not its object/);
  expect(() => readRegion({ ...index, chunks: { bad: index.chunks['0_-1'] } })).toThrow(/bad chunk/);
});

test('chunk distance is measured to the square, zero inside it', () => {
  expect(chunkDistance('0_-1', 250, 100, -100)).toBe(0);
  expect(chunkDistance('0_-1', 250, 300, -100)).toBe(50);
  expect(chunkDistance('0_-1', 250, -30, 40)).toBe(50);
});
