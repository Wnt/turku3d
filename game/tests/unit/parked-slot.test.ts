import { expect, test } from 'vitest';
import * as THREE from 'three';
import { slotRotation } from '../../src/world/parkedVehicles';

const q = (car: { yaw: number; pitch?: number; roll?: number }) => { const r = slotRotation(car); return new THREE.Quaternion(r.x, r.y, r.z, r.w); };

test('a level slot keeps the heading-only chassis rotation', () => {
  for (const yaw of [0, 0.7, 2, -2.5]) {
    const half = (yaw - Math.PI / 2) / 2;
    const r = slotRotation({ yaw });
    for (const [a, b] of [[r.x, 0], [r.y, Math.sin(half)], [r.z, 0], [r.w, Math.cos(half)]]) expect(a).toBeCloseTo(b, 12);
  }
});

test('pitch raises the nose and roll leans the car to its right, as the generator measures them', () => {
  for (const yaw of [0, 1.1, -2.2]) {
    // Model forward is (sin yaw, cos yaw) in x/z; the real-world right of it is (-cos yaw, sin yaw).
    const forward = new THREE.Vector3(Math.sin(yaw), 0, Math.cos(yaw)), right = new THREE.Vector3(-Math.cos(yaw), 0, Math.sin(yaw));
    const nose = new THREE.Vector3(1, 0, 0).applyQuaternion(q({ yaw, pitch: 0.1 }));
    expect(nose.dot(forward)).toBeCloseTo(Math.cos(0.1), 9);
    expect(nose.y).toBeCloseTo(Math.sin(0.1), 9);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q({ yaw, roll: 0.05 }));
    expect(up.dot(right)).toBeCloseTo(Math.sin(0.05), 9);
    expect(new THREE.Vector3(1, 0, 0).applyQuaternion(q({ yaw, roll: 0.05 })).dot(forward)).toBeCloseTo(1, 9);
  }
});
