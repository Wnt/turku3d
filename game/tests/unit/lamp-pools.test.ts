// Lamp pools: the nearest street lamps drive the fixed point slots without pops, never through buildings, and
// never by day. Program and cache-key stability while the slots move is in night-pipelines.test.ts.
import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { LampPools, POOL, footprintSight, nearest, poolLamp, poolWeights, type PoolLamp } from '../../src/render/lights/pools';
import { Footprint, scanTile, scanTileNow } from '../../src/render/lights/tileScan';
import type { Lamp } from '../../src/render/lights/lampField';

/** Street lamps every 30 m along z = 0, heads 7.6 m up. */
const row = (count: number, spacing = 30): PoolLamp[] => Array.from({ length: count }, (_, i) =>
  ({ key: `l${i}`, x: i * spacing, y: 5, z: 0, r: 1, g: 0.8, b: 0.58, intensity: 20 }));
/** Fill one triangle into a footprint grid, as the tile scan does. */
function fillTriangle(grid: Footprint, xs: number[], zs: number[]) {
  grid.fill(xs, zs, (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2]));
}
const slots = (n: number) => Array.from({ length: n }, () => new THREE.PointLight(0xffffff, 0, 40, 2));

/** Drive pools along a path at 60 Hz; returns per-frame slot states. */
function drive(pools: LampPools, lights: THREE.PointLight[], path: (t: number) => [number, number], seconds: number, night = 1) {
  const frames: { key: string | null; intensity: number; position: number[] }[][] = [];
  for (let f = 0; f <= seconds * 60; f++) {
    const t = f / 60, [x, z] = path(t), [nx] = path(t + 0.01);
    pools.update(x, z, nx - x || 1, 0, night, t * 1000);
    frames.push(pools.snapshot().map((s, i) => ({ key: s.key, intensity: lights[i].intensity, position: lights[i].position.toArray() })));
  }
  return frames;
}

describe('lamp pools', () => {
  test('nearest keeps the count + 1 closest, ascending; weights fall to 0 at the first lamp left out', () => {
    const lamps = row(10);
    const ranked = nearest(lamps, l => (l.x - 61) ** 2, 4);
    expect(ranked.map(r => r.item.key)).toEqual(['l2', 'l3', 'l1', 'l4', 'l0']);
    expect(ranked.map(r => r.d)).toEqual([1, 29, 31, 59, 61]);
    const weights = poolWeights(ranked, 4);
    // l0 is the first lamp left out (61 m): l4 at 59 m is almost as far and nearly dark, ready to hand over.
    expect([...weights.keys()]).toEqual(['l2', 'l3', 'l1', 'l4']);
    expect(weights.get('l2')!.weight).toBe(1);
    expect(weights.get('l1')!.weight).toBe(1);
    expect(weights.get('l4')!.weight).toBeCloseTo((61 - 59) / POOL.margin, 9);
    // Beyond reach nothing is lit.
    expect(poolWeights(nearest(lamps, l => (l.x - 5000) ** 2, 4), 4).size).toBe(0);
  });

  test('driving past a row of lamps never pops: slots fade, and change lamp only when dark', () => {
    const lights = slots(4), pools = new LampPools(lights);
    pools.setLamps(row(40));
    const frames = drive(pools, lights, t => [t * 15, 4], 60); // 15 m/s for 900 m
    const step = 20 / 60 / POOL.fade * 1.5 + 1e-9; // smoothstep's steepest slope is 1.5
    let handovers = 0;
    for (let f = 1; f < frames.length; f++) {
      frames[f].forEach((slot, i) => {
        const before = frames[f - 1][i];
        expect(Math.abs(slot.intensity - before.intensity), `frame ${f} slot ${i}`).toBeLessThanOrEqual(step);
        // A slot takes a new lamp only once the old one has faded out (below 0.5 % of its peak, invisible).
        if (slot.key !== before.key) { handovers++; expect(before.intensity, `frame ${f} slot ${i}`).toBeLessThan(0.1); expect(slot.intensity).toBe(0); }
        // Moving a light while it is lit would be a jump of its pool.
        if (slot.intensity > 0 && before.intensity > 0) expect(slot.position).toEqual(before.position);
      });
    }
    expect(handovers).toBeGreaterThan(25); // about one per 30 m lamp passed
    // The lamps around the camera are lit at full strength most of the time.
    const lit = frames.map(slots => slots.filter(s => s.intensity > 19).length);
    expect(lit.filter(n => n >= 2).length / lit.length).toBeGreaterThan(0.9);
    expect(lights).toHaveLength(4);
  });

  test('by day every slot is 0 (out of the shader loop); the night level scales the pools', () => {
    const lights = slots(4), pools = new LampPools(lights);
    pools.setLamps(row(10));
    drive(pools, lights, () => [60, 3], 3, 0);
    expect(lights.every(l => l.intensity === 0)).toBe(true);
    drive(pools, lights, () => [60, 3], 3, 0.5);
    expect(Math.max(...lights.map(l => l.intensity))).toBeCloseTo(10, 6);
    drive(pools, lights, () => [60, 3], 3, 1);
    expect(Math.max(...lights.map(l => l.intensity))).toBeCloseTo(20, 6);
    // No slots (mobile, low): nothing to drive.
    const none = new LampPools([]);
    none.setLamps(row(10)); none.update(0, 0, 1, 0, 1, 0);
    expect(none.snapshot()).toEqual([]);
  });

  test('lamps ahead of the camera rank first', () => {
    const lights = slots(2), pools = new LampPools(lights);
    pools.setLamps(row(11, 10).map(l => ({ ...l, x: l.x - 50 })));
    drive(pools, lights, () => [0, 0], 2); // looking toward +x
    const keys = pools.snapshot().map(s => s.key).sort();
    expect(keys).toEqual(['l6', 'l7']); // x = 10 and 20 (ahead), not x = -10
  });

  test('a lamp behind a building takes no slot; one at the end of an open street does', () => {
    const grid = new Footprint(-50, -50, 50, 50);
    // A building block between the camera (0, 0) and the lamp at (0, 30): z 10..20, x -20..20.
    fillTriangle(grid, [-20, 20, 20], [10, 10, 20]);
    fillTriangle(grid, [-20, 20, -20], [10, 20, 20]);
    expect(grid.covers(0, 15)).toBe(true); expect(grid.covers(0, 5)).toBe(false); expect(grid.covers(100, 15)).toBe(false);
    const sight = footprintSight(() => [grid]);
    expect(sight(0, 0, 0, 30)).toBe(true);
    expect(sight(0, 0, 30, 0)).toBe(false);
    // The ends of the segment are free: a camera against a wall, a lamp head reaching over a kerb.
    expect(sight(0, 8, 0, 30)).toBe(true);
    expect(sight(0, 8.5, 30, 8.5)).toBe(false);
    const lights = slots(1), pools = new LampPools(lights, sight);
    pools.setLamps([{ ...row(1)[0], key: 'behind', x: 0, z: 30 }, { ...row(1)[0], key: 'street', x: 35, z: 0 }]);
    drive(pools, lights, () => [0, 0], 2);
    expect(pools.snapshot()[0].key).toBe('street');
  });

  test('scanned building roofs fill the footprint grid', () => {
    const model = new THREE.Group();
    const roof = new THREE.BufferGeometry();
    roof.setAttribute('position', new THREE.Float32BufferAttribute([10, 12, 10, 30, 12, 10, 30, 12, 30, 10, 12, 30], 3));
    roof.setIndex([0, 2, 1, 0, 3, 2]);
    const mesh = new THREE.Mesh(roof, new THREE.MeshStandardMaterial());
    mesh.userData = { layer: 'buildings', object_id: 'buildings:a' };
    model.add(mesh);
    expect(scanTileNow(model, []).footprint).toBeUndefined();
    const job = scanTile(model, [], [], 4096, [0, 0, 40, 40]);
    let step = job.next();
    while (!step.done) step = job.next();
    const result = step.value;
    expect(result.footprint!.covers(20, 20)).toBe(true);
    expect(result.footprint!.covers(5, 20)).toBe(false);
    expect(result.footprint!.covers(31, 20)).toBe(false);
  });

  test('only street and bridge lamps light pools, hung below their heads', () => {
    const light = { x: 1, y: 7.6, z: 2, radius: 1.2, r: 5, g: 4, b: 2.9, day: 0 };
    const lamp = (kind: Lamp['kind']): Lamp => ({ key: kind, x: 1, y: 0, z: 2, yaw: 0, scale: 1, post: kind === 'street', kind, light });
    const street = poolLamp(lamp('street'))!;
    expect(street.y).toBeCloseTo(7.6 * (1 - POOL.drop), 9);
    expect([street.r, street.g, street.b]).toEqual([1, 0.8, 0.58]);
    expect(poolLamp(lamp('bridge'))!.y).toBeCloseTo(7.6 - POOL.bridgeDrop, 9);
    expect(poolLamp(lamp('wall'))).toBeNull();
    expect(poolLamp(lamp('tunnel'))).toBeNull();
  });
});
