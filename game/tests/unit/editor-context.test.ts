import { expect, test } from 'vitest';
import { bearing, groundUnder, measureContext, nearestRoads, type Raycast } from '../../src/dev/editor/context';
import { MAT } from '../../src/shared/col/materials';
import type { RoadGraphJson } from '../../src/gameplay/roadGraph';

// An east-west street along z = 0 (carriageway |z| < 4 m), kerbs at |z| 4..4.2, sidewalk beyond, a facade at z = -12.
const raycast: Raycast = async (o, d) => {
  if (d.y < 0) {
    const z = Math.abs(o.z);
    if (Math.abs(o.x - 1) < 0.05 && Math.abs(o.z + 6) < 0.05 && o.y > 1.5) return { toi: 1, point: { x: o.x, y: o.y - 1, z: o.z }, material: MAT.street_furniture };
    const material = z < 4 ? MAT.road : z < 4.2 ? MAT.curb : z < 12 ? MAT.sidewalk : MAT.building;
    return { toi: o.y, point: { x: o.x, y: 0, z: o.z }, material };
  }
  if (d.z < -0.5) { const toi = (o.z + 12) / -d.z; return { toi, point: { x: o.x + d.x * toi, y: o.y, z: -12 }, material: MAT.building }; }
  return null;
};
// Rows hold interior points only (the end points are nodes): the street bends through (0, 0), the walk is straight.
const graph: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [-50, -50, 50, 50],
  nodes: [-50, 0, 50, 0, -50, -8, 50, -8],
  edges: [[0, 1, 2 | (2 << 3), 100, 0, 0], [2, 3, 1, 100]], edgeIds: ['street', 'walk'],
  edgeAttrColumns: ['way', 'width'], edgeAttrs: [[123, 8], [456, 2.5]] };

test('ground under a bench looks through its own collision', async () => {
  const hit = await groundUnder(raycast, 1, 0, -6);
  expect(hit?.material).toBe(MAT.sidewalk); expect(hit?.point.y).toBe(0);
});

test('road graph: centreline, carriageway edge (negative on it) and the side of the digitised direction', () => {
  const north = nearestRoads([graph], 10, -6), on = nearestRoads([graph], 10, 1.5);
  expect(north.road).toMatchObject({ edge_id: 'street', way: 123, class: 'street', centreline_m: 6, edge_m: 2, side: 'left', direction_deg: 90 });
  expect(north.road!.bearing_deg).toBe(180);
  expect(north.path).toMatchObject({ edge_id: 'walk', centreline_m: 2, side: 'right', foot: true, car: false });
  expect(on.road).toMatchObject({ edge_m: -2.5, side: 'right' });
  expect(bearing(1, 0)).toBe(90); expect(bearing(0, 1)).toBe(180);
});

test('context of a sidewalk bench: surface, kerb and road distances, the facade, nearby props', async () => {
  const c = await measureContext({ raycast, roads: () => [graph],
    props: () => [{ key: 'self', type: 'bench', objectId: 'a', base: { x: 1, y: 0, z: -6 } }, { key: 'b', type: 'bin', objectId: 'b', base: { x: 3, y: 0, z: -6 } }],
    pointCloud: () => ({ setVisible() {}, isVisible: () => true, setOpacity() {}, getOpacity: () => 1, setPointSize() {}, getPointSize: () => 2,
      queryNear: (x, z, r) => ({ count: 42, maxHeightM: 0.9, at: [x, z, r] }) }) }, { x: 1, y: 0, z: -6 }, 'self');
  expect(c.surface).toBe('sidewalk');
  expect(c.surfaces.curb.distance_m).toBe(2); expect(c.surfaces.curb.bearing_deg).toBe(180);
  expect(c.surfaces.road.distance_m).toBe(2.5);
  expect(c.obstacles.building.distance_m).toBe(6); expect(c.obstacles.building.bearing_deg).toBe(0);
  expect(c.road?.edge_m).toBe(2);
  expect(c.props).toEqual([{ type: 'bin', object_id: 'b', distance_m: 2, bearing_deg: 90 }]);
  expect(c.lidar).toMatchObject({ count: 42 });
});
