import { describe, expect, test } from 'vitest';
import { pedestrianStop, vehicleStop, type TrafficPedestrian, type TrafficVehicle } from '../../src/traffic/pedestrians';

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

describe('traffic braking for players\' vehicles', () => {
  const car = (change: Partial<TrafficVehicle> = {}): TrafficVehicle =>
    ({ id: 'local', x: 20, y: 0, z: 0, hx: 1, hz: 0, vx: 0, vz: 0, length: 4.2, width: 1.8, ...change });
  const brake = (vehicles: TrafficVehicle[], time = 100) =>
    vehicleStop(0, 12, 4.5, 1.8, 0, time, { time: 100, vehicles }, path);

  test('stops behind a player car standing in the lane, with a gap', () => {
    const { stop, id } = brake([car()]);
    expect(id).toBe('local');
    expect(stop).toBeLessThan(20 - (4.5 + 4.2) / 2 - 1);
    expect(stop).toBeGreaterThan(20 - (4.5 + 4.2) / 2 - 3.5);
  });

  test('brakes for a player car predicted to cut across its path', () => {
    expect(brake([car({ x: 24, z: 12, hx: 0, hz: -1, vz: -6 })]).stop).toBeLessThan(24);
  });

  test('ignores a player car beside it in the next lane, behind it, or on a bridge above', () => {
    expect(brake([car({ z: 3.4 })]).stop).toBe(Infinity);
    expect(brake([car({ x: -4 })]).stop).toBe(Infinity);
    expect(brake([car({ y: 6 })]).stop).toBe(Infinity);
  });

  test('a player car pulling away ahead at speed does not hold it', () => {
    expect(brake([car({ vx: 14 })]).stop).toBe(Infinity);
  });

  test('stale snapshots leave no permanent obstacle', () => {
    expect(brake([car()], 103).stop).toBe(Infinity);
  });
});
