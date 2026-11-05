import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from '../../src/actors/fleet/visuals';
import type { ParkedVehicle } from '../../src/world/manifest';
import type { VehicleState } from '../../src/physics/protocol';

// A traffic car the player takes is claimed in the worker and its record added to the manifest at once, but the
// parked batches load it later (GPU preparation). Until then traffic/client.ts drew it from its raw physics body:
// on a loaded Mac most of a drive, stepping at worker cadence while the camera followed the render pose.
test('a just-claimed traffic car is drawn as the driven actor at the given pose before its batch load', () => {
  const scene = new THREE.Scene(), visuals = new ParkedCarVisuals(scene);
  const record: ParkedVehicle = { object_id: 'tr:t0_6_-1:4e14cce5:4435283', model: 'sedan', color: 'silver', x: 10, y: 0, z: 5, yaw: 0,
    width: 1.85, length: 4.55, height: 1.48, clear_front_m: 10, clear_rear_m: 10 };
  visuals.takeOver(record.object_id);
  expect(visuals.renders(record.object_id)).toBe(false); // the old call: no definition yet, nothing taken over
  visuals.takeOver(record.object_id, record);
  expect(visuals.renders(record.object_id)).toBe(true);
  const at = (x: number): VehicleState => ({ id: record.object_id, pos: { x, y: 0.75, z: 5 }, rot: { x: 0, y: 0, z: 0, w: 1 },
    linvel: { x: 10, y: 0, z: 0 }, speed: 10, forwardSpeed: 10, curbStrike: null, wheels: [] });
  const camera = new THREE.PerspectiveCamera();
  for (const x of [10, 10.17, 10.33]) {
    visuals.update([at(x)]);
    expect(visuals.inspect(record.object_id, camera)!.position[0]).toBeCloseTo(x, 6);
  }
});
