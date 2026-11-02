import { describe, expect, test } from 'vitest';
import { DataUtils } from 'three/webgpu';
import { GroundLampField, groundLampField } from '../../src/render/lights/groundField';
import { Footprint } from '../../src/render/lights/tileScan';
import type { Lamp } from '../../src/render/lights/lampField';

const lamp = (x: number, z = 0): Lamp => ({ key: `${x}:${z}`, x, y: 4, z, yaw: 0, scale: 1,
  post: true, kind: 'street', light: { x, y: 12, z, r: 5, g: 4, b: 3, radius: 1, day: 0 } });
function value(x: number, z: number, channel = 0) {
  const origin = groundLampField.origin.value;
  const image = groundLampField.map.image as { data: Uint16Array };
  return DataUtils.fromHalfFloat(image.data[(Math.floor((z - origin.y) / 2) * 512 + Math.floor((x - origin.x) / 2)) * 4 + channel]);
}
function settle(field: GroundLampField, x = 0, z = 0) {
  for (let i = 0; i < 1000; i++) { field.update(x, z, 1); if (!field.pending) return; }
  throw new Error('Lamp raster did not finish');
}
describe('mapped lamp ground irradiance', () => {
  test('near and 300m-away lamps keep equal pools as the camera travels', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0), lamp(300)]); settle(field);
    const near = value(1, 1), far = value(301, 1);
    // Projected inverse-square irradiance at the texel centre, one metre in X and Z.
    const expected = 20 * 8 / (2 + 8 * 8) ** 1.5 * (1 - (2 / (22 * 22)) ** 2) ** 2 * (64 / 66) ** 2;
    expect(near).toBeCloseTo(expected, 3); expect(far).toBeCloseTo(near, 5);
    settle(field, 300);
    expect(value(1, 1)).toBeCloseTo(near, 5);
    expect(value(301, 1)).toBeCloseTo(far, 5);
    expect(value(301, 1, 3)).toBe(4);
  });
  test('downward optics preserve dark gaps instead of broad grazing spill', () => {
    const field = new GroundLampField(() => []);
    field.setLamps([lamp(0)]); settle(field);
    expect(value(9, 1)).toBeLessThan(value(1, 1) * .08);
    expect(value(23, 1)).toBe(0);
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
});
