import { expect, test } from 'vitest';
import { REFINE_3D_ABOVE, refineDistance, sceneryTiles, sceneryBounds, sceneryContains, sceneryDistance, skylinePriority, type SceneryView } from '../../src/world/sceneryView';

const heading = 273 * Math.PI / 180;
const view: SceneryView = { x: 3751.26, y: 164.15, z: 430.05, dx: Math.sin(heading), dz: -Math.cos(heading), halfAngle: .8 };
test('kilometre cut covers the distant chimney without overlapping parent and child geometry', () => {
  const wanted = sceneryTiles(6000, view);
  expect(wanted.some(id => sceneryContains(id, 't0_-6_5'))).toBe(true);
  expect(wanted.some(id => sceneryContains(id, 't0_30_1'))).toBe(true); // Coarse reserve behind the camera.
  expect(wanted.length).toBeLessThan(500);
  for (const a of wanted) for (const b of wanted) if (a !== b) expect(sceneryContains(a, b)).toBe(false);
});
test('8 and 15 km use bounded outer rings and preserve fine near coverage in every direction', () => {
  for (const radius of [8000, 15000]) for (let angle = 0; angle < 2 * Math.PI; angle += Math.PI / 4) {
    const pose = { ...view, x: -150, z: -150, dx: Math.sin(angle), dz: Math.cos(angle) };
    const ids = sceneryTiles(radius, pose);
    expect(ids.length).toBeLessThan(600);
    expect(ids).toContain('t0_-1_-1');
    expect(ids.some(id => sceneryBounds(id).level === 4)).toBe(true);
    expect(ids.every(id => sceneryDistance(id, pose.x, pose.z) <= radius)).toBe(true);
    for (let d = 250; d < radius; d += 250) {
      const leaf = `t0_${Math.floor((pose.x + pose.dx * d) / 250)}_${Math.floor((pose.z + pose.dz * d) / 250)}`;
      expect(ids.filter(id => sceneryContains(id, leaf))).toHaveLength(1);
    }
  }
});
test('skyline priority brings tall distant silhouettes ahead of foreground terrain', () => {
  const source = (tile: string, height: number) => ({ tile, h: [0, height] as [number, number], key: 'test', source_sha256: 'test' });
  expect(skylinePriority(source('t2_-2_1', 107), view)).toBeGreaterThan(skylinePriority(source('t0_9_1', 30), view));
});

test('a rapid turn has a complete coarse city cut in every direction', () => {
  const pose = {...view, x:125,z:125};
  const wanted=sceneryTiles(4000,pose);
  for(let angle=0;angle<Math.PI*2;angle+=Math.PI/16)for(let distance=0;distance<4000;distance+=125){
    const leaf=`t0_${Math.floor((pose.x+Math.sin(angle)*distance)/250)}_${Math.floor((pose.z+Math.cos(angle)*distance)/250)}`;
    expect(wanted.filter(id=>sceneryContains(id,leaf))).toHaveLength(1);
  }
});

test('projected detail refines for zoom and leaves two-kilometre cells coarse', () => {
  const pose = {...view, x:125, z:125, dx:1, dz:0, halfAngle:.8, focalPixels:600};
  const ordinary = sceneryTiles(4000, pose);
  const zoomed = sceneryTiles(4000, {...pose, focalPixels:2400});
  const count = (ids: string[]) => ids.filter(id => sceneryBounds(id).level === 0).length;
  expect(count(ordinary)).toBeLessThan(count(zoomed));
  expect(ordinary).toContain('t2_2_0');
  expect(ordinary).toContain('t0_0_0');
  for (const ids of [ordinary, zoomed]) for (const a of ids) for (const b of ids)
    if (a !== b) expect(sceneryContains(a,b)).toBe(false);
});


test('the projected-detail cut has hysteresis across small resolution changes', () => {
  const pose = {...view, x:125, z:125, dx:1, dz:0, halfAngle:.8};
  const fine = sceneryTiles(4000, {...pose, focalPixels:1200});
  expect(fine).toContain('t0_4_0');
  expect(sceneryTiles(4000, {...pose, focalPixels:1100})).toContain('t2_1_0');
  expect(sceneryTiles(4000, {...pose, focalPixels:1100}, fine)).toContain('t0_4_0');
  expect(sceneryTiles(4000, {...pose, focalPixels:800}, fine)).toContain('t2_1_0');
});

test('above 250 m the quadtree refines on 3D distance: a high flier sees 1 km nodes where a low one sees 250 m cells', () => {
  const pose = { x: 125, z: 125, dx: 1, dz: 0, halfAngle: .8, focalPixels: 600 };
  const cells = (y: number) => sceneryTiles(4000, { ...pose, y }).filter(id => sceneryBounds(id).level === 0).length;
  // Continuous: no change up to the threshold, then fewer cells as the camera climbs (GTA: only LOD/SLOD from 400 m).
  expect(refineDistance(300, 200)).toBe(300); expect(refineDistance(300, REFINE_3D_ABOVE)).toBe(300);
  expect(refineDistance(300, 650)).toBe(500);
  expect(cells(240)).toBe(cells(20));
  // The square under the camera keeps its 250 m cell well past the full-tile switch (450 m 3D), so a resident full
  // tile always hands over to its own cell; from about 830 m (650 m beyond the threshold, past the 580 m floor)
  // only 1 km nodes draw below.
  for (const y of [500, 700]) expect(sceneryTiles(4000, { ...pose, y })).toContain('t0_0_0');
  expect(cells(900)).toBe(0); expect(cells(240)).toBeGreaterThan(0);
  for (const y of [20, 500, 1200]) { const ids = sceneryTiles(4000, { ...pose, y });
    for (const a of ids) for (const b of ids) if (a !== b) expect(sceneryContains(a, b)).toBe(false); }
});
