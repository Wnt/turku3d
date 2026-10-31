import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { CharacterInterpolation, VehicleInterpolation } from '../../src/actors/interpolation';
import { ChaseCamera } from '../../src/vehicle/chaseCamera';
import { OrbitCamera } from '../../src/actors/camera';
import type { CharacterState, VehicleState } from '../../src/physics/protocol';

const character = (x: number): CharacterState => ({ pos: { x, y: 0, z: 0 }, vel: { x: 3, y: 0, z: 0 }, grounded: true, enabled: true, groundMaterial: 0 });
const vehicle = (x: number): VehicleState => ({ pos: { x, y: 0, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 },
  linvel: { x: 3, y: 0, z: 0 }, speed: 3, forwardSpeed: 3, curbStrike: null,
  wheels: [{ pos: { x: 1, y: -0.4, z: 0.8 }, steer: 0, rotation: x, contact: true, suspension: 0 }] });

for (const velocity of [[0, 0, -72], [0, 0, -288], [0, -50, 0]]) {
  test(`orbit camera holds airborne character framing at ${velocity.join(',')} m/s through uneven frames`, () => {
    const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 6000);
    const orbit = new OrbitCamera(camera);
    const focus = new THREE.Vector3(2000, 300, 500);
    const movement = new THREE.Vector3(...velocity);
    orbit.update(1 / 60, focus);
    for (let frame = 0; frame < 120; frame++) {
      const dt = [1 / 120, 1 / 60, 1 / 30, 0.08][frame % 4];
      focus.addScaledVector(movement, dt);
      orbit.update(dt, focus);
      camera.updateMatrixWorld();
      const projected = focus.clone().project(camera);
      expect(projected.x).toBeCloseTo(0, 8);
      expect(projected.y).toBeCloseTo(0, 8);
      expect(camera.position.distanceTo(focus)).toBeCloseTo(orbit.distance, 8);
    }
  });
}

test('walking and driving sample adjacent physics ticks with one bounded render clock', () => {
  const walker = new CharacterInterpolation(), car = new VehicleInterpolation();
  const dt = 1 / 60;
  for (let tick = 1; tick <= 20; tick++) {
    const now = tick * dt;
    walker.push(character(now * 3), character((tick - 1) * dt * 3), tick, 0, now);
    car.push(vehicle(now * 3), vehicle((tick - 1) * dt * 3), tick, 0, now);
    for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
      const time = now + fraction * dt;
      const expected = ((tick - 1) + fraction) * dt * 3;
      expect(walker.sample(time)!.pos.x).toBeCloseTo(expected, 6);
      expect(car.sample(time)!.pos.x).toBeCloseTo(expected, 6);
      expect(car.sample(time)!.wheels[0].rotation).toBeCloseTo(expected, 6);
    }
  }
  expect(car.sample(100)!.pos.x).toBeCloseTo(1);
});

test('rotating chase camera keeps moving car centered while collision replies arrive late', async () => {
  const replies: ((distance: number) => void)[] = [];
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 100);
  const cast = vi.fn(() => new Promise<number>(resolve => replies.push(resolve)));
  const chase = new ChaseCamera(camera, { cameraCast: cast });
  for (let frame = 0; frame < 30; frame++) {
    const pose = vehicle(frame * 0.05);
    chase.update(1 / 60, pose, 0.04, 0, false);
    camera.updateMatrixWorld();
    const center = new THREE.Vector3(pose.pos.x, pose.pos.y + 0.9, pose.pos.z).project(camera);
    expect(Math.abs(center.x)).toBeLessThan(1e-5);
    expect(Math.abs(center.y)).toBeLessThan(1e-5);
    if (replies.length) {
      const before = camera.position.clone();
      replies.shift()!(6.5);
      await Promise.resolve();
      expect(camera.position.distanceTo(before)).toBe(0);
    }
  }
});
