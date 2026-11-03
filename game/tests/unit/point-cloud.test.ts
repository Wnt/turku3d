// Point-cloud overlay (render/pointCloud): COPC parsing and LAZ decode on a synthetic PDAL writers.copc fixture
// (6001 generated points, no survey data), alignment with the engine frame, and the LOD budget traversal.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createLazPerf } from 'laz-perf';
import { HEADER_BYTES, childKeys, decodeRecords, decompressChunk, nodeAnchor, nodeBounds, parseHeader, parseHierarchyPage,
  type LazPerfModule } from '../../src/render/pointCloud/copc';
import { shufflePoints } from '../../src/render/pointCloud/shuffle';
import { drawnPoints, selectNodes, type LodNode, type LodView } from '../../src/render/pointCloud/lod';
import { OFFICE_EN, enToEngine } from '../../src/core/origin';

const file = readFileSync(new URL('../fixtures/pointcloud/synthetic.copc.laz', import.meta.url));
const bytes = file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength);
const slice = (offset: number, size: number) => bytes.slice(offset, offset + size);

describe('COPC reading', () => {
  const header = parseHeader(slice(0, HEADER_BYTES));
  const page = parseHierarchyPage(slice(header.rootPage.offset, header.rootPage.size));

  it('parses the LAS 1.4 header and COPC info', () => {
    expect(header.pointFormat).toBe(7);
    expect(header.recordLength).toBe(36);
    expect(header.pointCount).toBe(6001);
    expect(header.scale).toEqual([0.01, 0.01, 0.01]);
    expect(header.min[0]).toBeCloseTo(23459658, 0);
    expect(header.max[1]).toBeCloseTo(6704264, 0);
    expect(header.spacing).toBeGreaterThan(0);
  });

  it('reads a hierarchy whose node counts sum to the header count', () => {
    expect(page.pages).toEqual([]);
    expect(page.nodes.reduce((n, node) => n + node.pointCount, 0)).toBe(header.pointCount);
    const root = page.nodes.find(n => n.key === '0-0-0-0')!;
    expect(root).toBeDefined();
    // Every non-root node's parent is in the hierarchy.
    const keys = new Set(page.nodes.map(n => n.key));
    for (const n of page.nodes) if (n.depth) expect([...keys].some(k => childKeys(parseKey(k)).includes(n.key))).toBe(true);
  });

  it('places node bounds inside the octree cube and children inside parents', () => {
    const root = nodeBounds(header, { depth: 0, x: 0, y: 0, z: 0 });
    const child = nodeBounds(header, { depth: 1, x: 1, y: 0, z: 1 });
    expect(root[3] - root[0]).toBeCloseTo(2 * header.cube.half, 6);
    expect(child[0]).toBeCloseTo((root[0] + root[3]) / 2, 6);
    expect(child[5]).toBeCloseTo(root[5], 6);
  });

  it('decodes every node; the marker point lands on the pilot address in the engine frame', async () => {
    const laz = await (createLazPerf as unknown as () => Promise<LazPerfModule>)();
    let total = 0, found: number[] | null = null;
    for (const node of page.nodes) {
      const records = decompressChunk(laz, new Uint8Array(slice(node.offset, node.byteSize)), node.pointCount, header.pointFormat, header.recordLength);
      const anchor = nodeAnchor(header, node);
      const points = decodeRecords(records, node.pointCount, header, anchor);
      total += points.count;
      for (let i = 0; i < points.count; i++) {
        const [r, g, b, cls] = points.colors.subarray(i * 4, i * 4 + 4);
        if (r === 255 && g === 0 && b === 0 && cls === 6)
          found = [points.positions[i * 3] + anchor[0], points.positions[i * 3 + 1] + anchor[1], points.positions[i * 3 + 2] + anchor[2]];
      }
      expect(points.minY).toBeGreaterThanOrEqual(1.99);
      expect(points.maxY).toBeLessThanOrEqual(8.01);
    }
    expect(total).toBe(6001);
    // The fixture writes the marker at E 23459668, N 6704254, H 3.69 (game/src/qa/street-cameras.json: engine (168, 246)).
    const expected = enToEngine(OFFICE_EN.E, OFFICE_EN.N, 3.69);
    expect(found).not.toBeNull();
    expect(found![0]).toBeCloseTo(expected.x, 2);
    expect(found![1]).toBeCloseTo(expected.y, 2);
    expect(found![2]).toBeCloseTo(expected.z, 2);
    expect(expected.x).toBe(168);
    expect(expected.z).toBe(246);
  });

  it('shuffles all columns with one permutation, deterministically', () => {
    const count = 1000;
    const make = () => {
      const positions = new Float32Array(count * 3), colors = new Uint8Array(count * 4), extra = new Uint8Array(count * 4);
      for (let i = 0; i < count; i++) { positions[i * 3] = i; colors[i * 4] = i & 255; colors[i * 4 + 1] = i >> 8; extra[i * 4] = i & 255; }
      return { count, positions, colors, extra };
    };
    const a = make(), b = make();
    shufflePoints(a, '3-1-2-0'); shufflePoints(b, '3-1-2-0');
    expect(a.positions).toEqual(b.positions);
    let moved = 0;
    for (let i = 0; i < count; i++) {
      const id = a.positions[i * 3];
      expect(a.colors[i * 4] | (a.colors[i * 4 + 1] << 8)).toBe(id);
      expect(a.extra[i * 4]).toBe(id & 255);
      if (id !== i) moved++;
    }
    expect(moved).toBeGreaterThan(count * 0.9);
  });
});

function parseKey(key: string) {
  const [depth, x, y, z] = key.split('-').map(Number);
  return { depth, x, y, z };
}

describe('point-cloud LOD', () => {
  /** A 2-level quadtree tile at (x0, z0), 500 m wide, root spacing 3.4 m, ground at y 0..30. */
  function tile(x0: number, z0: number, loaded = true): LodNode {
    const child = (i: number): LodNode => {
      const x = x0 + (i & 1) * 250, z = z0 + (i >> 1) * 250;
      return { key: `1-${i}`, depth: 1, pointCount: 170_000, box: [x, 0, z, x + 250, 30, z + 250], spacing: 1.7, children: [], loaded };
    };
    return { key: '0', depth: 0, pointCount: 67_000, box: [x0, 0, z0, x0 + 500, 30, z0 + 500], spacing: 3.4, children: [0, 1, 2, 3].map(child), loaded };
  }
  const view = (x: number, z: number): LodView => ({ x, y: 2, z, focal: 935, visible: () => true });
  const options = { budget: 1_000_000, refinePx: 2.5, thinPx: 1.25, maxDistance: 1500 };

  it('refines the tile the camera stands in and thins far roots', () => {
    const near = tile(0, 0), far = tile(1500, 0);
    const s = selectNodes([near, far], view(100, 100), options);
    expect(s.selected[0]).toBe(near);
    expect(s.selected).toContain(near.children[0]);
    // The far root, 1400 m away, projects 3.4 * 935 / 1400 = 2.3 px: drawn whole (above thinPx) but not refined.
    expect(s.selected).toContain(far);
    expect(s.selected.some(n => far.children.includes(n))).toBe(false);
    expect(s.points).toBeLessThanOrEqual(options.budget);
  });

  it('never exceeds the point budget and keeps parents before children', () => {
    const tiles = [tile(0, 0), tile(500, 0), tile(0, 500), tile(500, 500)];
    const s = selectNodes(tiles, view(500, 500), { ...options, budget: 400_000 });
    expect(s.points).toBeLessThanOrEqual(400_000);
    for (const node of s.selected) {
      const parent = tiles.find(t => t.children.includes(node));
      if (parent) expect(s.selected.indexOf(parent)).toBeLessThan(s.selected.indexOf(node));
    }
  });

  it('does not refine nodes that are not loaded yet', () => {
    const t = tile(0, 0, false);
    const s = selectNodes([t], view(100, 100), options);
    expect(s.selected).toEqual([t]);
  });

  it('skips tiles beyond the distance limit or outside the frustum', () => {
    const far = tile(3000, 0);
    expect(selectNodes([far], view(0, 0), options).selected).toEqual([]);
    expect(selectNodes([tile(0, 0)], { ...view(100, 100), visible: () => false }, options).selected).toEqual([]);
  });

  it('thins by screen area below the thinning spacing', () => {
    const node = tile(0, 0);
    expect(drawnPoints(node, 2, 1.25)).toBe(67_000);
    expect(drawnPoints(node, 0.625, 1.25)).toBe(Math.ceil(67_000 / 4));
  });
});
