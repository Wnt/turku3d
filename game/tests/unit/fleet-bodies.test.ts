import './fleet-assets.fixture';
import { expect, test } from 'vitest';
import type * as THREE from 'three/webgpu';
import { AUTHORED_MODELS } from '../../src/actors/fleet/registry';
import { loadAuthoredFleet, loadAuthoredFleetParts } from '../../src/actors/fleet/authored';

const triangles = (geometry: THREE.BufferGeometry) => (geometry.index?.count ?? geometry.getAttribute('position').count) / 3;

// Issue 038: cars drawn as four wheels without a body. Every shipped authored asset must keep its body.
test('every authored fleet asset has a full body at every LOD, around its four hubs', async () => {
  for (const id of AUTHORED_MODELS.map(model => model.id)) for (const lod of [0, 1, 2]) {
    const whole = (await loadAuthoredFleet(id, lod))!, parts = lod < 2 ? await loadAuthoredFleetParts(id, lod) : null;
    const body = parts?.body ?? whole, positions = body.getAttribute('position');
    expect(positions.count, `${id}/${lod} body vertices`).toBeGreaterThan(1000);
    expect(Array.from(positions.array).every(Number.isFinite), `${id}/${lod} finite`).toBe(true);
    for (const name of ['normal', 'color', 'fleetSurface']) expect(body.getAttribute(name)?.count, `${id}/${lod} ${name}`).toBe(positions.count);
    body.computeBoundingBox();
    const box = body.boundingBox!;
    // A real car body, not a hub ring: metres long (a microcar is 2.2 m), wide and tall (a 1965 coupe is 1.3 m), sitting at road height.
    expect(box.max.z - box.min.z, `${id}/${lod} length`).toBeGreaterThan(2);
    expect(box.max.x - box.min.x, `${id}/${lod} width`).toBeGreaterThan(1.4);
    expect(box.max.y, `${id}/${lod} height`).toBeGreaterThan(1.2);
    if (parts) {
      expect(parts.wheels).toHaveLength(4);
      for (const wheel of parts.wheels) {
        expect(wheel.pivot.z).toBeGreaterThan(box.min.z); expect(wheel.pivot.z).toBeLessThan(box.max.z);
        expect(Math.abs(wheel.pivot.x)).toBeLessThan(box.max.x);
      }
      expect(triangles(parts.body) + parts.wheels.reduce((n, wheel) => n + triangles(wheel.geometry), 0)).toBe(triangles(whole));
    }
  }
}, 60_000);
