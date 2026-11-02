import { describe, expect, test } from 'vitest';
import { pedestrianStop, type TrafficPedestrian } from '../../src/traffic/pedestrians';

const path = (s: number) => ({ x: s, z: 0, hx: 1, hz: 0 });
const person = (change: Partial<TrafficPedestrian> = {}): TrafficPedestrian =>
  ({ id: 1, x: 10, y: 0, z: 3, vx: 0, vz: -1.4, ...change });
const stop = (people: TrafficPedestrian[], crossings: { s: number; signal: boolean }[] = [], time = 100) =>
  pedestrianStop(0, 7, 4.5, 1.8, 0, time, { time: 100, people }, path, crossings, 7);

describe('traffic yielding to shared pedestrian trajectories', () => {
  test('brakes before a pedestrian predicted to enter the driving path', () => {
    const s = stop([person()]);
    expect(s).toBeGreaterThan(0);
    expect(s).toBeLessThan(10 - 4.5 / 2);
  });

  test('gives a waiting person clearance at an unsignalized mapped zebra', () => {
    expect(stop([person({ z: 4, vz: 0, waiting: true })], [{ s: 10, signal: false }]))
      .toBeCloseTo(10 - 4.5 / 2 - 1.4);
  });

  test('a stationary person waiting at the signalized curb does not hold car traffic', () => {
    expect(stop([person({ z: 4, vz: 0, waiting: true })], [{ s: 10, signal: true }])).toBe(Infinity);
  });

  test('still yields to someone already walking across a signalized crossing', () => {
    expect(stop([person()], [{ s: 10, signal: true }])).toBeLessThan(10 - 4.5 / 2);
  });

  test('does not brake for a pedestrian on a bridge above the road', () => {
    expect(stop([person({ y: 5 })], [{ s: 10, signal: false }])).toBe(Infinity);
  });

  test('stale snapshots do not leave an invisible permanent obstacle', () => {
    expect(stop([person()], [{ s: 10, signal: false }], 103)).toBe(Infinity);
  });

  test('a pedestrian moving away along the curb does not stop the car', () => {
    expect(stop([person({ x: 8, z: 4, vx: 1.4, vz: 0 })], [{ s: 10, signal: false }])).toBe(Infinity);
  });
});
