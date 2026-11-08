import { describe, expect, test, vi } from 'vitest';
import { DataUtils } from 'three/webgpu';
import { GroundLampField, LAMP_FIELD, batwing, groundLampField, lampIntensity } from '../../src/render/lights/groundField';
import { Footprint } from '../../src/render/lights/tileScan';
import type { Lamp } from '../../src/render/lights/lampField';

const lamp = (x: number, z = 0): Lamp => ({ key: `${x}:${z}`, x, y: 4, z, yaw: 0, scale: 1,
  post: true, kind: 'street', light: { x, y: 12, z, r: 5, g: 4, b: 3, radius: 1, day: 0 } });
/** A lamp hung on a span wire: no post, its record sits at the light itself. */
const hung = (x: number, y: number, z = 0): Lamp => ({ key: `${x}:${z}`, x, y, z, yaw: 0, scale: 1,
  post: false, kind: 'street', light: { x, y, z, r: 5, g: 4.3, b: 3.4, radius: 1.2, day: 0 } });
/** A field texel: `half` 0 is the ground (rgb, ground height), 1 the wall (light vector x/z, sum, head height). */
function value(x: number, z: number, channel = 0, half = 0) {
  const origin = groundLampField.origin.value;
  const image = groundLampField.map.image as { data: Uint16Array };
  const i = Math.floor((x - origin.x) / 2), j = Math.floor((z - origin.y) / 2);
  return DataUtils.fromHalfFloat(image.data[(j * 1024 + i + half * 512) * 4 + channel]);
}
function settle(field: GroundLampField, x = 0, z = 0) {
  for (let i = 0; i < 1000; i++) { field.update(x, z, 1); if (!field.pending) return; }
  throw new Error('Lamp raster did not finish');
}
/** Ground irradiance of a lamp `height` above its ground at horizontal distance squared `r2` (its peak channel). */
const groundAt = (height: number, r2: number) => {
  const d2 = r2 + height * height, c = height / Math.sqrt(d2);
  return lampIntensity(height) * batwing(c) * c / d2 * (1 - (r2 / LAMP_FIELD.radius ** 2) ** 2) ** 2;
};
describe('mapped lamp ground irradiance', () => {
  test('near and 300m-away lamps keep equal pools as the camera travels', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0), lamp(300)]); settle(field);
    const near = value(1, 1), far = value(301, 1);
    // Batwing intensity, cosine on the ground and inverse square at the texel centre, one metre in X and Z.
    expect(near).toBeCloseTo(groundAt(8, 2), 3); expect(far).toBeCloseTo(near, 5);
    expect(near).toBeCloseTo(LAMP_FIELD.nadir, 1);
    settle(field, 300);
    expect(value(1, 1)).toBeCloseTo(near, 5);
    expect(value(301, 1)).toBeCloseTo(far, 5);
    expect(value(301, 1, 3)).toBe(4);
  });
  test('pools of lamps 30 m apart overlap along the street; nothing beyond the radius', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0), lamp(30)]); settle(field);
    // Between two 8 m lamps the street keeps a third to a half of the light under one: pools, no black gap.
    expect(value(15, 1)).toBeGreaterThan(value(1, 1) * .3);
    expect(value(15, 1)).toBeLessThan(value(1, 1) * .55);
    field.setLamps([lamp(0)]); settle(field);
    expect(value(9, 1)).toBeGreaterThan(value(1, 1) * .4);
    expect(value(LAMP_FIELD.radius + 1, 1)).toBe(0);
  });
  test('a lamp hung over the street lights the ground below it, the same as a post of its height', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([hung(0, 20)]); settle(field);
    expect(value(1, 1, 3)).toBe(20 - LAMP_FIELD.hang);
    expect(value(1, 1)).toBeCloseTo(groundAt(LAMP_FIELD.hang, 2), 3);
    expect(value(1, 1, 2) / value(1, 1)).toBeCloseTo(3.4 / 5, 2);
  });
  test('walls beside a lamp get a light vector toward it, the lamp head height, and a tail like the ground', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([hung(0, 20, 0)]); settle(field);
    // East of the lamp the light comes from the west (-x); its magnitude is the sum channel.
    const ex = value(7, 1, 0, 1), ez = value(7, 1, 1, 1), sum = value(7, 1, 2, 1);
    expect(ex).toBeLessThan(0); expect(Math.abs(ez)).toBeLessThan(Math.abs(ex) * .2);
    expect(sum).toBeCloseTo(Math.hypot(ex, ez), 2);
    expect(value(7, 1, 3, 1)).toBe(20);
    expect(value(-7, 1, 0, 1)).toBeCloseTo(-ex, 3);
    expect(value(21, 1, 2, 1)).toBeLessThan(sum * .5); expect(value(LAMP_FIELD.radius + 1, 1, 2, 1)).toBe(0);
    // Two lamps facing each other across a wall point: the vectors cancel, the sum keeps both.
    field.setLamps([hung(-8, 20), hung(8, 20)]); settle(field);
    expect(Math.abs(value(1, 1, 0, 1))).toBeLessThan(value(1, 1, 2, 1) * .2);
  });
  test('building footprints stop a pool crossing a wall', () => {
    const wall = new Footprint(6, -32, 10, 32); wall.cells.fill(1);
    const field = new GroundLampField(() => [wall]);
    field.setLamps([lamp(0)]); settle(field);
    expect(value(-15, 1)).toBeGreaterThan(0);
    expect(value(15, 1)).toBe(0);
  });
  test('day is off and removed lamps cannot leave stale irradiance', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0)]); settle(field);
    field.update(0, 0, 0); expect(groundLampField.night.value).toBe(0);
    field.setLamps([]); settle(field); expect(value(1, 1)).toBe(0);
    field.dispose(); expect(groundLampField.ready.value).toBe(0);
  });
  test('only publishes completed maps across streaming and camera-cell changes', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0)]); settle(field);
    const original = value(1, 1), version = groundLampField.map.version;
    field.setLamps(Array.from({length: 900}, (_, i) => lamp((i % 30) * 20 - 300, Math.floor(i / 30) * 20 - 300)));
    field.update(150, 0, 1);
    expect(field.pending).toBe(true);
    expect(groundLampField.map.version).toBe(version);
    expect(value(1, 1)).toBe(original);
    // Restart while incomplete, with a different set: no intermediate field is uploaded.
    field.setLamps([lamp(300)]); settle(field, 150);
    expect(value(1, 1)).toBe(0); expect(value(301, 1)).toBeGreaterThan(.1);
    expect(groundLampField.map.version).toBe(version + 1);
    field.setLamps(Array.from({length: 900}, (_, i) => lamp(i % 30 * 20, Math.floor(i / 30) * 20)));
    field.update(150, 0, 1); expect(field.pending).toBe(true);
    field.update(150, 0, 0); expect(field.pending).toBe(false);
    expect(groundLampField.night.value).toBe(0);
  });
  test('tiles streaming beyond the square never restart its build; a change inside it does', () => {
    const grid = new Footprint(-20, -20, 20, 20); grid.cells[0] = 1;
    const grids = [grid];
    const field = new GroundLampField(() => grids);
    const builds = vi.spyOn(field as unknown as { rasterize: () => Generator<void> }, 'rasterize');
    const near = Array.from({ length: 200 }, (_, i) => lamp((i % 20) * 20 - 200, Math.floor(i / 20) * 20 - 100));
    field.setLamps(near); field.update(0, 0, 1);
    expect(field.pending).toBe(true);
    // A tile 2 km away arrives with its lamps and buildings: the build in progress carries on, and a complete map stays.
    const far = new Footprint(2000, 2000, 2250, 2250); far.cells.fill(1); grids.push(far);
    field.setLamps([...near, lamp(2100, 2100), lamp(2120, 2100)]);
    settle(field);
    const version = groundLampField.map.version;
    field.setLamps([...near, lamp(2140, 2100)]); field.update(0, 0, 1);
    expect(field.pending).toBe(false); expect(groundLampField.map.version).toBe(version);
    expect(builds).toHaveBeenCalledTimes(1);
    // One more lamp inside it rebuilds.
    field.setLamps([...near, lamp(40, 40)]); settle(field);
    expect(builds).toHaveBeenCalledTimes(2);
    expect(groundLampField.map.version).toBe(version + 1); expect(value(41, 41)).toBeGreaterThan(.1);
  });
  test('a lamp skips the sight test only where no footprint cell can cover a sample', () => {
    let seed = 5; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const grid = new Footprint(-41.3, 17.9, 40, 80);
    for (let i = 0; i < grid.cells.length; i++) grid.cells[i] = random() < 0.01 ? 1 : 0;
    for (let n = 0; n < 300; n++) {
      const x0 = -60 + random() * 110, z0 = 0 + random() * 90, x1 = x0 + random() * 12, z1 = z0 + random() * 12;
      let sampled = false;
      for (let x = x0; x <= x1 && !sampled; x += 0.25) for (let z = z0; z <= z1; z += 0.25) if (grid.covers(x, z)) { sampled = true; break; }
      if (sampled) expect(grid.coversAny(x0, z0, x1, z1)).toBe(true);
      if (!grid.coversAny(x0, z0, x1, z1)) expect(sampled).toBe(false);
    }
    // A wall beyond the pool radius leaves the pool as if there were none.
    const far = new Footprint(LAMP_FIELD.radius + 6, -32, LAMP_FIELD.radius + 10, 32); far.cells.fill(1);
    const open = new GroundLampField(() => []), walled = new GroundLampField(() => [far]);
    open.setLamps([lamp(0)]); settle(open); const pool = value(15, 1);
    walled.setLamps([lamp(0)]); settle(walled); expect(value(15, 1)).toBe(pool);
  });
});
