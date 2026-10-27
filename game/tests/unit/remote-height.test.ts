import { expect, test } from 'vitest';
import { remoteColliderHeight, remoteRootHeight, VEHICLE_POSE_HEIGHT } from '../../src/net/height';

test('network height maps foot, car, scooter and truck poses to their visual and collider origins', () => {
  expect(remoteRootHeight('foot')).toBe(0);
  expect(remoteRootHeight('car')).toBe(VEHICLE_POSE_HEIGHT);
  expect(remoteRootHeight('scooter')).toBe(VEHICLE_POSE_HEIGHT);
  expect(remoteRootHeight('truck')).toBe(VEHICLE_POSE_HEIGHT);
  expect(VEHICLE_POSE_HEIGHT).toBe(0.6);
  expect(remoteColliderHeight('foot')).toBeCloseTo(0.92);
  expect(remoteColliderHeight('car')).toBe(0);
  expect(remoteColliderHeight('scooter')).toBe(0);
});
