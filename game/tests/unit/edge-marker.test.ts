// The visible world edge: the unbuilt mask over the L0 grid and the far LOD materials it styles (render/unbuilt.ts),
// and the striped band along the true edges (world/edgeMarker.ts, CollisionStreamer.trueEdges).
import { test, expect, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { UnbuiltArea } from '../../src/render/unbuilt';
import { EdgeMarker, edgeLine } from '../../src/world/edgeMarker';
import { CollisionStreamer } from '../../src/world/collisionStreaming';

const renderer = { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer;

test('built cells are the published and drafted L0 tiles; far LOD models get the unbuilt look and give it back', () => {
  const area = new UnbuiltArea(renderer);
  area.setBuilt(['t0_0_0', 't0_1_0', 't1_0_0', 't0_-1_2']);
  expect(area.built(10, 10)).toBe(true);
  expect(area.built(260, 249)).toBe(true);
  expect(area.built(-1, 600)).toBe(true);
  expect(area.built(510, 10)).toBe(false);
  expect(area.built(1e6, 0)).toBe(false);
  const version = area.mask.version;
  area.setBuilt(['t0_1_0', 't0_0_0', 't0_-1_2']); // same set: the mask is not uploaded again
  expect(area.mask.version).toBe(version);

  const source = new THREE.MeshStandardMaterial();
  const far = new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), source));
  const near = new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), source));
  area.apply(far, 't1_1_1');
  area.apply(near, 't0_0_0');
  const styled = (far.children[0] as THREE.Mesh).material as THREE.MeshStandardNodeMaterial;
  expect(styled).not.toBe(source);
  expect(styled.colorNode).toBeTruthy();
  expect((near.children[0] as THREE.Mesh).material).toBe(source); // full-detail tiles keep their materials
  const dispose = vi.spyOn(styled, 'dispose');
  area.release(far);
  expect((far.children[0] as THREE.Mesh).material).toBe(source);
  expect(dispose).toHaveBeenCalled();
});

test('true edges are the sides of loaded tiles with no collision in any source', () => {
  const collision = Object.fromEntries(['c_0_0', 'c_1_0', 'c_2_0'].map(id => [id, { url: `${id}.col`, bytes: 4 }]));
  const loaded = new Map<string, unknown>([['c_0_0', {}], ['c_1_0', {}]]);
  const s = new CollisionStreamer({ collision, baseUrl: 'http://x/' } as never, { loaded, setKnown: () => {} } as never, 100);
  // c_2_0 exists (not loaded): no edge towards it. Everything else around is the true edge.
  expect(s.trueEdges().map(e => e.join(' ')).sort()).toEqual(['0 0 1', '0 0 2', '0 0 3', '1 0 2', '1 0 3']);
});

test('the marker band follows the ground along each true edge and leaves with it', async () => {
  const line = edgeLine(1, 0, 0, 3);
  expect(line).toEqual([{ x: 125, z: 0, sx: 124.6, sz: 0 }, { x: 125, z: 31.25, sx: 124.6, sz: 31.25 }, { x: 125, z: 62.5, sx: 124.6, sz: 62.5 }]);
  const probes: number[][] = [];
  const rays = { raycast: async (o: { x: number; y: number; z: number }) => {
    probes.push([o.x, o.z, o.y]);
    return o.z > 40 ? null : { point: { x: o.x, y: 2 + o.z / 100, z: o.z } };
  } };
  const marker = new EdgeMarker(rays, { c_1_0: { h: [2, 3] } });
  let edges: [number, number, number][] = [[1, 0, 0], [1, 0, 3]];
  marker.update(() => edges, 0);
  await new Promise(r => setTimeout(r, 0));
  expect(marker.count).toBe(2);
  expect(probes.every(([, , top]) => top === 9)).toBe(true);
  const band = marker.group.children.find(c => c.name === 'edge 1_0_0') as THREE.Mesh;
  const pos = band.geometry.getAttribute('position');
  // Bottom 0.3 m under the ground, top 1.1 m above it; past z 40 the rays miss and the last height carries on.
  expect(pos.getX(0)).toBe(125);
  expect(pos.getY(0)).toBeCloseTo(1.7, 5);
  expect(pos.getY(1)).toBeCloseTo(3.1, 5);
  const lastTop = pos.getY(pos.count - 1);
  expect(lastTop).toBeCloseTo(pos.getY(pos.count - 3), 5);
  edges = [[1, 0, 3]];
  marker.update(() => edges, 100); // throttled: nothing changes within 250 ms
  expect(marker.count).toBe(2);
  marker.update(() => edges, 300);
  expect(marker.count).toBe(1);
  expect(band.parent).toBeNull();
  marker.dispose();
});
