// The autopilot (gameplay/autopilot): path queries, the lane router on a synthetic junction, and the drive follower
// steering the real Rapier vehicle (physics/world.ts) on the committed dev tiles through VehicleInput only.
import { describe, expect, test } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { CAR, FOOT, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { curvatureAt, makePath, project, sampleAt } from '../../src/gameplay/autopilot/path';
import { DriveFollower } from '../../src/gameplay/autopilot/follow';
import { LaneRouter } from '../../src/gameplay/autopilot/lanes';
import { Autopilot, vehicleGeometry, vehiclePose, type AutopilotWorld } from '../../src/gameplay/autopilot/autopilot';
import { emptyActions } from '../../src/input/bindings';

const DIR = path.resolve(__dirname, '../../public/dev-tiles');
const manifest = JSON.parse(fs.readFileSync(path.join(DIR, 'manifest.json'), 'utf8'));
const load = (id: string) => { const b = fs.readFileSync(path.join(DIR, manifest.collision[id].url)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer; };
const G = 2.0;

async function devWorld() {
  const pw = new PhysicsWorld(RAPIER);
  for (const id of Object.keys(manifest.collision)) await pw.loadTile(id, load(id));
  pw.world.step();
  return pw;
}

/** A 4-way junction tile (same shape as the traffic tests' fixture), seams on all four sides. */
function junction(lanes = 1): RoadGraphJson {
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2,
    bbox: [0, 0, 250, 250], nodes: [125, 125, 0, 125, 250, 125, 125, 0, 125, 250],
    nodeFlags: [0, NODE.SEAM, NODE.SEAM, NODE.SEAM, NODE.SEAM],
    edges: [1, 2, 3, 4].map(n => [n, 0, CAR | FOOT | 3 << 3, 125]),
    edgeIds: ['west', 'east', 'north', 'south'], bits: { highways: ['secondary'] },
    edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
    edgeAttrs: Array.from({ length: 4 }, () => [lanes, lanes, lanes * 7, 40, 11, 0, 0, 9]),
  };
}

describe('autopilot paths', () => {
  test('projection reports the side of travel and curvature the turn direction (x east, z south)', () => {
    const p = makePath([{ x: 0, z: 0 }, { x: 50, z: 0 }, { x: 50, z: 50 }]);
    // Heading east, +z (south) is the right-hand side; turning from east to south is a right turn.
    expect(project(p, 10, 2).lateral).toBeCloseTo(2);
    expect(project(p, 10, -2).lateral).toBeCloseTo(-2);
    expect(curvatureAt(p, 50, 3)).toBeGreaterThan(0.1);
    expect(curvatureAt(p, 20, 3)).toBeCloseTo(0);
  });
  test('a projection with a surface height ignores a deck far above or below', () => {
    const p = makePath([{ x: 0, z: 0, y: 10 }, { x: 50, z: 0, y: 10 }]);
    expect(project(p, 10, 1, 0, Infinity, 2).distance).toBe(Infinity);
    expect(project(p, 10, 1, 0, Infinity, 9).distance).toBeCloseTo(1);
  });
});

describe('lane router', () => {
  const router = new LaneRouter();
  router.setTile({ tile: 't0_0_0', json: junction(2), hash: 'fixture' });

  test('routes over the junction movements from the right-hand lane and keeps to the lanes', () => {
    // From the west approach heading east (eastbound lanes are south of the centreline) to the north exit.
    const route = router.route({ x: 30, z: 128, hx: 1, hz: 0 }, { x: 122, z: 20 })!;
    expect(route).not.toBeNull();
    const p = route.path;
    expect(p.length).toBeGreaterThan(150);
    expect(p.length).toBeLessThan(260);
    // On the carriageways all the way (within 8 m of a centreline) and ending near the goal.
    for (let s = 0; s <= p.length; s += 2) {
      const at = sampleAt(p, s);
      expect(Math.min(Math.abs(at.x - 125), Math.abs(at.z - 125))).toBeLessThan(8);
    }
    expect(Math.hypot(p.points.at(-2)! - 122, p.points.at(-1)! - 20)).toBeLessThan(8);
    // A left turn leaves from the lane next to the divider (k 0): the route starts on, or changes onto, it.
    expect(route.lanes.length).toBeGreaterThanOrEqual(2);
    expect(route.limit(10)).toBeCloseTo(40 / 3.6, 0);
  });

  test('never snaps a car onto the oncoming lane', () => {
    const near = router.snap({ x: 30, z: 123, hx: 1, hz: 0 })!;
    // Heading east: the nearest lane by distance is westbound (z < 125), the snap must take an eastbound one.
    const i = near.lane.points;
    expect(i[2] - i[0]).toBeGreaterThan(0);
  });

  test('follows one lane exactly, extended to enter and leave it at speed', () => {
    const lane = router.lanes().find(l => l.start.kind === 'junction' && l.end.kind === 'seam' && l.points[0] > 126 && l.points.at(-2)! > 200)!;
    const route = router.follow(lane, 60, 30)!;
    expect(route.laneStart).toBeGreaterThan(40);
    expect(route.laneEnd - route.laneStart).toBeCloseTo(lane.length, 0);
    // Every point of the lane lies on the route within a few centimetres.
    for (let i = 0; i < lane.cum.length; i++) {
      expect(project(route.path, lane.points[2 * i], lane.points[2 * i + 1]).distance).toBeLessThan(0.05);
    }
  });
});

describe('drive follower on the real vehicle', () => {
  test('tracks a straight and a turn within lane tolerance and stops at the end', async () => {
    const pw = await devWorld();
    // East along z = 100, then a 25 m radius right turn... seen from above x east, z south, heading east, a turn to
    // -z (north) is a left turn.
    const pts = [{ x: 30, z: 100 }];
    for (let x = 40; x <= 170; x += 10) pts.push({ x, z: 100 });
    for (let a = 0; a <= 90; a += 7.5) pts.push({ x: 170 + 25 * Math.sin(a * Math.PI / 180), z: 75 + 25 * Math.cos(a * Math.PI / 180) });
    for (let z = 65; z >= 25; z -= 10) pts.push({ x: 195, z });
    const route = makePath(pts);
    pw.spawnVehicle({ x: 30, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const follower = new DriveFollower(route, vehicleGeometry('car'), { speed: 12 });
    let status = 'driving', maxLateralStraight = 0, maxLateralTurn = 0, top = 0, steps = 0;
    for (; steps < 60 * 60 && status !== 'arrived'; steps++) {
      const v = pw.vehicleState()!;
      const step = follower.step(vehiclePose(v), 1 / 60);
      status = step.status;
      if (follower.s > 20 && follower.s < 135) maxLateralStraight = Math.max(maxLateralStraight, Math.abs(step.lateral));
      if (follower.s > 140 && follower.s < route.length - 5) maxLateralTurn = Math.max(maxLateralTurn, Math.abs(step.lateral));
      top = Math.max(top, v.speed);
      pw.fixedStep(null, step.command);
    }
    const end = pw.vehicleState()!;
    expect(status).toBe('arrived');
    expect(top).toBeGreaterThan(10);
    expect(maxLateralStraight).toBeLessThan(0.35);
    expect(maxLateralTurn).toBeLessThan(0.9);
    expect(Math.hypot(end.pos.x - 195, end.pos.z - 25)).toBeLessThan(3);
    expect(end.speed).toBeLessThan(1);
    expect(follower.recoveries).toBe(0);
    pw.world.free();
  }, 30_000);

  test('backs out when a wall blocks the path', async () => {
    const pw = await devWorld();
    // Straight into the dev building block (x 192–212, z 200–216).
    const route = makePath([{ x: 170, z: 208 }, { x: 240, z: 208 }]);
    pw.spawnVehicle({ x: 170, y: G + 0.8, z: 208 }, 0);
    const follower = new DriveFollower(route, vehicleGeometry('car'), { speed: 6 });
    const seen = new Set<string>();
    let minX = Infinity, maxX = 0;
    for (let k = 0; k < 60 * 14; k++) {
      const v = pw.vehicleState()!;
      const step = follower.step(vehiclePose(v), 1 / 60);
      seen.add(step.status);
      maxX = Math.max(maxX, v.pos.x);
      if (seen.has('recovering')) minX = Math.min(minX, v.pos.x);
      pw.fixedStep(null, step.command);
    }
    expect(seen.has('stuck')).toBe(true);
    expect(seen.has('recovering')).toBe(true);
    expect(follower.recoveries).toBeGreaterThan(0);
    // It reversed away from the wall at least a metre.
    expect(maxX - minX).toBeGreaterThan(1);
    pw.world.free();
  }, 30_000);
});

describe('reverse-out ground check', () => {
  test('the checked arc is the one the car backs along, and a drop behind it blocks the reverse', async () => {
    const { reversePoints, reverseClear } = await import('../../src/gameplay/autopilot/reverseCheck');
    const pw = await devWorld();
    const geometry = vehicleGeometry('car');
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 40; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pose = vehiclePose(pw.vehicleState()!);
    // Back out with full right lock (the brake pedal reverses at a standstill), as the follower's reverse-out does.
    for (let k = 0; k < 60 * 1.8; k++) pw.fixedStep(null, { throttle: 0, steer: 1, brake: 0.7, handbrake: false });
    const end = vehiclePose(pw.vehicleState()!);
    const rows = reversePoints(pose, 1, geometry);
    // The car's lateral drift (right of its start heading) has the sign of the checked arc's.
    const right = (p: { x: number; z: number }) => (p.x - pose.x) * -pose.hz + (p.z - pose.z) * pose.hx;
    expect(Math.sign(right(end))).toBe(Math.sign(right(rows[rows.length - 1][1])));
    const ray = async (o: { x: number; y: number; z: number }, d: { x: number; y: number; z: number }, max: number) => pw.raycast(o, d, max, 'solid');
    expect(await reverseClear(pose, 0, geometry, ray)).toBe(true);
    // A quay 3 m behind the rear axle: the ground ends there (a canal below the edge).
    const quay = async (o: { x: number; y: number; z: number }, d: { x: number; y: number; z: number }, max: number) =>
      (o.x - pose.x) * pose.hx + (o.z - pose.z) * pose.hz < -geometry.rearOffset - 3 ? null : pw.raycast(o, d, max, 'solid');
    expect(await reverseClear(pose, 0, geometry, quay)).toBe(false);
    pw.world.free();
  }, 30_000);

  test('a stuck car with a drop behind ends its trip stuck instead of backing over the edge', async () => {
    const pw = await devWorld();
    pw.spawnVehicle({ x: 170, y: G + 0.8, z: 208 }, 0);
    // Nosed into the block the car stands at x ~190: the ground ends 3-4 m behind it (a quay edge), nothing at x < 184.
    const ray = async (o: { x: number; y: number; z: number }, d: { x: number; y: number; z: number }, max: number) =>
      o.x < 184 ? null : pw.raycast(o, d, max, 'solid');
    const pilot = new Autopilot({ mode: () => 'car', vehicle: () => pw.vehicleState(), feet: () => ({ x: 0, y: 0, z: 0 }),
      cameraBasis: () => ({ fx: 0, fz: -1, rx: 1, rz: 0 }), lanes: new LaneRouter(), footRoute: (a, b) => [a, b], groundRay: ray });
    // Straight into the dev building block (x 192–212, z 200–216).
    const trip = pilot.goTo({ x: 240, z: 208 }, { mode: 'drive', path: [{ x: 170, z: 208 }, { x: 240, z: 208 }], speed: 6 });
    let maxX = 0, backed = 0;
    for (let k = 0; k < 60 * 20 && trip.state !== 'failed'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const x = pw.vehicleState()!.pos.x;
      maxX = Math.max(maxX, x); backed = Math.max(backed, maxX - x);
      await new Promise(r => setTimeout(r, 0));
    }
    expect(trip.state).toBe('failed');
    expect(trip.reason).toBe('stuck: no safe way back');
    // It never backed toward the edge.
    expect(backed).toBeLessThan(0.5);
    pw.world.free();
  }, 30_000);

  test('with drivable ground behind, the checked reverse-out still backs the car away', async () => {
    const pw = await devWorld();
    pw.spawnVehicle({ x: 170, y: G + 0.8, z: 208 }, 0);
    const pilot = new Autopilot({ mode: () => 'car', vehicle: () => pw.vehicleState(), feet: () => ({ x: 0, y: 0, z: 0 }),
      cameraBasis: () => ({ fx: 0, fz: -1, rx: 1, rz: 0 }), lanes: new LaneRouter(), footRoute: (a, b) => [a, b],
      groundRay: async (o, d, max) => pw.raycast(o, d, max, 'solid') });
    const trip = pilot.goTo({ x: 240, z: 208 }, { mode: 'drive', path: [{ x: 170, z: 208 }, { x: 240, z: 208 }], speed: 6 });
    let maxX = 0, backed = 0;
    for (let k = 0; k < 60 * 14 && backed < 1; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const x = pw.vehicleState()!.pos.x;
      maxX = Math.max(maxX, x); backed = Math.max(backed, maxX - x);
      await new Promise(r => setTimeout(r, 0));
    }
    expect(trip.progress.recoveries).toBeGreaterThan(0);
    expect(backed).toBeGreaterThan(1);
    pw.world.free();
  }, 30_000);
});

describe('autopilot controller', () => {
  const world = (mode: 'foot' | 'car'): AutopilotWorld => ({
    mode: () => mode, vehicle: () => null, feet: () => ({ x: 0, y: 0, z: 0 }),
    cameraBasis: () => ({ fx: 0, fz: -1, rx: 1, rz: 0 }), lanes: new LaneRouter(),
    footRoute: (a, b) => [a, b],
  });
  test('walks through the stick: camera-relative, and a human stick takes over', async () => {
    const pilot = new Autopilot(world('foot'));
    const trip = pilot.goTo({ x: 20, z: 0 });
    await Promise.resolve();
    const a = emptyActions();
    pilot.control(a, 1 / 60);
    // Camera looks north (-z): east is the stick's right.
    expect(a.moveX).toBeCloseTo(1);
    expect(a.moveY).toBeCloseTo(0);
    for (let k = 0; k < 30; k++) pilot.control(emptyActions(), 1 / 60);
    const human = emptyActions(); human.moveY = 1;
    pilot.control(human, 1 / 60);
    expect((await trip.done).state).toBe('cancelled');
    expect(trip.reason).toMatch(/^takeover/);
  });
  test('driving on foot fails at once', async () => {
    const trip = new Autopilot(world('foot')).goTo({ x: 10, z: 0 }, { mode: 'drive' });
    expect((await trip.done).reason).toBe('not in a vehicle');
  });
});
