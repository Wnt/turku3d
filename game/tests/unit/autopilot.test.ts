// The autopilot (gameplay/autopilot): path queries, the lane router on a synthetic junction, and the drive follower
// steering the real Rapier vehicle (physics/world.ts) on the committed dev tiles through VehicleInput only.
import { describe, expect, test } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { CAR, FOOT, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { curvatureAt, makePath, project, sampleAt } from '../../src/gameplay/autopilot/path';
import { DriveFollower, type Obstacle } from '../../src/gameplay/autopilot/follow';
import { LaneRouter } from '../../src/gameplay/autopilot/lanes';
import { Autopilot, vehicleGeometry, vehiclePose, type AutopilotWorld } from '../../src/gameplay/autopilot/autopilot';
import { emptyActions } from '../../src/input/bindings';
import { MAT } from '../../src/shared/col/materials';

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

  test('a car on a one-way street starts on its own lane, never on a street 25 m behind it', () => {
    // A block loop: two-way streets west (x 75) and across (z 100, z 200); the east side (x 100) is one-way north.
    const ONE = CAR | FOOT | 4 | 2 << 3, TWO = CAR | FOOT | 2 << 3;
    const block: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2,
      bbox: [0, 0, 250, 250], nodes: [100, 200, 100, 100, 75, 100, 75, 200, 75, 250],
      nodeFlags: [NODE.JUNCTION, NODE.JUNCTION, NODE.JUNCTION, NODE.JUNCTION, NODE.SEAM],
      edges: [[0, 1, ONE, 100], [1, 2, TWO, 25], [2, 3, TWO, 100], [3, 0, TWO, 25], [3, 4, TWO, 50]],
      edgeIds: ['east', 'north', 'west', 'south', 'out'], bits: { highways: ['residential'] },
      edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
      edgeAttrs: [[1, 0, 4, 30, 2, 0, 4, 4], ...Array.from({ length: 4 }, () => [1, 1, 7, 30, 2, 0, 4, 4])] };
    const one = new LaneRouter();
    one.setTile({ tile: 't0_0_0', json: block, hash: 'block' });
    const route = one.route({ x: 100, z: 150, hx: 0, hz: -1 }, { x: 75, z: 240 })!;
    expect(route).not.toBeNull();
    // North along its own one-way lane first (x ~100), round the block: not 25 m west and straight south.
    expect(Math.abs(route.path.points[0] - 100)).toBeLessThan(6);
    expect(route.path.length).toBeGreaterThan(180);
    // A target beside the west street, 22 m from the car's own lane: round the block to it, not a stop 22 m short.
    const near = one.route({ x: 100, z: 150, hx: 0, hz: -1 }, { x: 78, z: 140 })!;
    expect(Math.hypot(near.path.points.at(-2)! - 78, near.path.points.at(-1)! - 140)).toBeLessThan(6);
    expect(near.path.length).toBeGreaterThan(90);
  });

  test('a turn in the street costs a route TURN_IN_STREET_M: round the block when that is shorter, forward-only on request', () => {
    // A two-way block (x 75..100, z 100..200) with a dead-end spur west from its south-west corner to x 40.
    const TWO = CAR | FOOT | 2 << 3;
    const block: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2,
      bbox: [0, 0, 250, 250], nodes: [100, 200, 100, 100, 75, 100, 75, 200, 40, 200],
      nodeFlags: [NODE.JUNCTION, NODE.JUNCTION, NODE.JUNCTION, NODE.JUNCTION, NODE.JUNCTION],
      edges: [[0, 1, TWO, 100], [1, 2, TWO, 25], [2, 3, TWO, 100], [3, 0, TWO, 25], [3, 4, TWO, 35]],
      edgeIds: ['east', 'north', 'west', 'south', 'spur'], bits: { highways: ['residential'] },
      edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
      edgeAttrs: Array.from({ length: 5 }, () => [1, 1, 7, 30, 2, 0, 4, 4]) };
    const r = new LaneRouter();
    r.setTile({ tile: 't0_0_0', json: block, hash: 'block' });
    const heading = (route: NonNullable<ReturnType<LaneRouter['route']>>, at: number) => {
      const s = sampleAt(route.path, at);
      return [Math.round(s.hx), Math.round(s.hz)];
    };
    // Heading north on the east street, the target 80 m behind: round the block (170 m) beats turning (80 m + 150).
    const round = r.route({ x: 102, z: 110, hx: 0, hz: -1 }, { x: 102, z: 190 })!;
    expect(heading(round, 3)).toEqual([0, -1]);
    expect(round.path.length).toBeGreaterThan(150);
    // The target 20 m behind: turning (20 m + 150) beats the block (230 m)...
    const turn = r.route({ x: 102, z: 150, hx: 0, hz: -1 }, { x: 102, z: 170 })!;
    expect(turn.path.length).toBeLessThan(60);
    // ...unless the turn found no room: then forward only, round the block.
    const forward = r.route({ x: 102, z: 150, hx: 0, hz: -1 }, { x: 102, z: 170 }, { forward: true })!;
    expect(heading(forward, 3)).toEqual([0, -1]);
    expect(forward.path.length).toBeGreaterThan(200);
    // Facing into the dead-end spur: turn where the car stands, not 40 m on at the dead end (both turn once).
    const spur = r.route({ x: 60, z: 202, hx: -1, hz: 0 }, { x: 102, z: 150 })!;
    expect(spur.path.length).toBeLessThan(110);
    // Forward only from there: no route (the spur's dead end is a U-turn).
    expect(r.route({ x: 60, z: 202, hx: -1, hz: 0 }, { x: 102, z: 150 }, { forward: true })).toBeNull();
  });

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

describe('obstacles', () => {
  // East along z = 100 on open ground; obstacles are footprints handed to the follower (follow.ts Obstacle).
  // (The dev world has a wall across z = 100 near x = 248.)
  const route = makePath([{ x: 30, z: 100 }, { x: 240, z: 100 }]);
  const car = vehicleGeometry('car'), half = car.back!, width = car.halfWidth!;
  const box = (x: number, z: number, vx = 0, traffic = true): Obstacle => ({ x, z, hx: 1, hz: 0, halfLength: 2.3, halfWidth: 0.95, vx, vz: 0, traffic });
  /** Bumper gap along x and side gap along z between the car and an obstacle (negative both: they overlap). */
  const gaps = (v: { pos: { x: number; z: number } }, o: Obstacle) =>
    [Math.abs(o.x - v.pos.x) - half - o.halfLength, Math.abs(o.z - v.pos.z) - width - o.halfWidth];

  test('follows a slower car at a gap, then passes a still car standing half in its lane', async () => {
    const pw = await devWorld();
    pw.spawnVehicle({ x: 30, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const follower = new DriveFollower(route, vehicleGeometry('car'), { speed: 14 });
    // A car 40 m ahead at 6 m/s that turns off at x 150; a parked car at x 200, 1.1 m into the lane (right of travel).
    const lead = box(70, 100, 6), parked = box(200, 101.1, 0, false);
    let followGap = Infinity, followSpeed = 0, parkedClear = Infinity, maxNudge = 0, passedAt = 0;
    for (let k = 0; k < 60 * 50; k++) {
      const v = pw.vehicleState()!, t = k / 60;
      lead.x = 70 + 6 * t;
      const obstacles = lead.x < 150 ? [lead, parked] : [parked];
      const step = follower.step(vehiclePose(v), 1 / 60, obstacles);
      if (lead.x < 150 && t > 4) { followGap = Math.min(followGap, gaps(v, lead)[0]); if (t > 11) followSpeed = v.speed; }
      const [along, side] = gaps(v, parked);
      if (along < 0) parkedClear = Math.min(parkedClear, side);
      maxNudge = Math.min(maxNudge, follower.nudge);
      if (!passedAt && v.pos.x > parked.x + 10) passedAt = v.speed;
      pw.fixedStep(null, step.command);
      if (step.status === 'arrived') break;
    }
    // A gap behind the lead (standstill 3.5 m + 1.1 s at 6 m/s ≈ 10 m), at its speed.
    expect(followGap).toBeGreaterThan(5);
    expect(followSpeed).toBeGreaterThan(5);
    expect(followSpeed).toBeLessThan(7.5);
    // Passed the parked car on its left, with room, and drove on at speed.
    expect(maxNudge).toBeLessThan(-0.5);
    expect(parkedClear).toBeGreaterThan(0.1);
    expect(passedAt).toBeGreaterThan(6);
    pw.world.free();
  }, 30_000);

  test('stops behind a car queued in its lane and never passes lane traffic', async () => {
    const pw = await devWorld();
    pw.spawnVehicle({ x: 30, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const follower = new DriveFollower(route, vehicleGeometry('car'), { speed: 14 });
    const queued = box(120, 100.4);
    let minGap = Infinity;
    for (let k = 0; k < 60 * 25; k++) {
      const v = pw.vehicleState()!;
      const step = follower.step(vehiclePose(v), 1 / 60, [queued]);
      minGap = Math.min(minGap, gaps(v, queued)[0]);
      pw.fixedStep(null, step.command);
    }
    const end = pw.vehicleState()!;
    expect(end.speed).toBeLessThan(0.3);
    expect(minGap).toBeGreaterThan(1.5);
    expect(gaps(end, queued)[0]).toBeLessThan(6);
    expect(follower.nudge).toBe(0);
    expect(follower.recoveries).toBe(0);
    pw.world.free();
  }, 30_000);

  test('brakes for a car crossing the path ahead', async () => {
    const pw = await devWorld();
    pw.spawnVehicle({ x: 30, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const follower = new DriveFollower(route, vehicleGeometry('car'), { speed: 14 });
    // Southbound across the route at x 150, timed to meet the car there.
    const crossing: Obstacle = { x: 150, z: 60, hx: 0, hz: 1, halfLength: 2.3, halfWidth: 0.95, vx: 0, vz: 7, traffic: true };
    let overlap = false, slowest = Infinity, k = 0, launched = false;
    for (; k < 60 * 30; k++) {
      const v = pw.vehicleState()!;
      // It sets off 40 m from the route when the car is 73 m short of the crossing: both get there in about 5 s.
      if (!launched && v.pos.x > 150 - 73) launched = true;
      if (launched) crossing.z += 7 / 60;
      const step = follower.step(vehiclePose(v), 1 / 60, crossing.z < 140 ? [crossing] : []);
      const dx = Math.abs(v.pos.x - crossing.x) - half - crossing.halfWidth, dz = Math.abs(v.pos.z - crossing.z) - width - crossing.halfLength;
      if (dx < 0 && dz < 0) overlap = true;
      if (launched && crossing.z < 110) slowest = Math.min(slowest, v.speed);
      pw.fixedStep(null, step.command);
    }
    expect(launched).toBe(true);
    expect(overlap).toBe(false);
    expect(slowest).toBeLessThan(10);
    pw.world.free();
  }, 30_000);
});

describe('turn in the street', () => {
  test('a path that leaves behind the car turns it round and arrives, without a stuck recovery', async () => {
    const pw = await devWorld();
    // Facing east, the route goes west: a U-turn.
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const follower = new DriveFollower(makePath([{ x: 100, z: 100 }, { x: 40, z: 100 }]), vehicleGeometry('car'), { speed: 8 });
    let status = 'driving', maxDz = 0;
    for (let k = 0; k < 60 * 40 && status !== 'arrived'; k++) {
      const v = pw.vehicleState()!;
      const step = follower.step(vehiclePose(v), 1 / 60);
      status = step.status;
      maxDz = Math.max(maxDz, Math.abs(v.pos.z - 100));
      pw.fixedStep(null, step.command);
    }
    const end = pw.vehicleState()!;
    expect(status).toBe('arrived');
    expect(follower.turns).toBeGreaterThan(0);
    expect(follower.recoveries).toBe(0);
    expect(Math.hypot(end.pos.x - 40, end.pos.z - 100)).toBeLessThan(3);
    // A tight turn: it swung out less than a car length and a half from the street's line.
    expect(maxDz).toBeLessThan(7);
    pw.world.free();
  }, 30_000);

  test('nosed up to a wall with the route behind, the autopilot turns back and forth until it faces the route', async () => {
    const pw = await devWorld();
    // Facing the dev building block (x 192–212, z 200–216) 3–4 m from its wall; the route runs back west.
    pw.spawnVehicle({ x: 186, y: G + 0.8, z: 208 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = new Autopilot({ mode: () => 'car', vehicle: () => pw.vehicleState(), feet: () => ({ x: 0, y: 0, z: 0 }),
      cameraBasis: () => ({ fx: 0, fz: -1, rx: 1, rz: 0 }), lanes: new LaneRouter(), footRoute: (a, b) => [a, b],
      groundRay: async (o, d, max) => pw.raycast(o, d, max, 'solid') });
    const trip = pilot.goTo({ x: 150, z: 208 }, { mode: 'drive', path: [{ x: 186, z: 208 }, { x: 150, z: 208 }], speed: 8 });
    for (let k = 0; k < 60 * 30 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      await new Promise(r => setTimeout(r, 0));
    }
    const end = pw.vehicleState()!;
    expect(trip.state).toBe('arrived');
    expect(Math.hypot(end.pos.x - 150, end.pos.z - 208)).toBeLessThan(4);
    // It turned in the street instead of driving into the wall and backing out of it as a stuck car.
    expect(trip.progress.recoveries).toBe(0);
    pw.world.free();
  }, 30_000);
});

describe('turn in the street on a carriageway', () => {
  const pilotOn = (pw: PhysicsWorld, ray: AutopilotWorld['groundRay']) => new Autopilot({ mode: () => 'car', vehicle: () => pw.vehicleState(),
    feet: () => ({ x: 0, y: 0, z: 0 }), cameraBasis: () => ({ fx: 0, fz: -1, rx: 1, rz: 0 }), lanes: new LaneRouter(),
    footRoute: (a, b) => [a, b], groundRay: ray });

  test('a U-turn keeps the car on a 7 m carriageway between pavements', async () => {
    const pw = await devWorld();
    // The dev ground is one surface: call it pavement more than 3.5 m either side of z = 100.
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && Math.abs(o.z - 100) > 3.5 ? { ...hit, material: MAT.sidewalk } : hit && { ...hit, material: MAT.road };
    };
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 101.5 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 50, z: 98.5 }, { mode: 'drive', path: [{ x: 100, z: 101.5 }, { x: 50, z: 98.5 }], speed: 8 });
    let maxOff = 0, fastest = 0, turning = true;
    for (let k = 0; k < 60 * 60 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const v = pw.vehicleState()!;
      // Turned once the chassis faces west (forward x = 1 - 2 (qy^2 + qz^2)).
      if (turning && 1 - 2 * (v.rot.y ** 2 + v.rot.z ** 2) < -0.6) turning = false;
      if (turning) { maxOff = Math.max(maxOff, Math.abs(v.pos.z - 100)); fastest = Math.max(fastest, v.speed); }
      await new Promise(r => setTimeout(r, 0));
    }
    expect(trip.state).toBe('arrived');
    expect(trip.progress.recoveries).toBe(0);
    // The car's middle stayed on the carriageway during the turn, at walking pace.
    expect(maxOff).toBeLessThan(3.5);
    expect(fastest).toBeLessThan(2.5);
    pw.world.free();
  }, 60_000);

  test('a path leaving at right angles from rest turns the car short of a quay edge ahead', async () => {
    const pw = await devWorld();
    // Facing east, the path leaves south; the ground ends 4 m ahead of the car's middle (a quay edge at x = 104).
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      if (o.x > 104) return null;
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && { ...hit, material: MAT.road };
    };
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 100, z: 150 }, { mode: 'drive', path: [{ x: 100, z: 100 }, { x: 100, z: 150 }], speed: 8 });
    let maxX = 0;
    const g = vehicleGeometry('car');
    for (let k = 0; k < 60 * 60 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const p = vehiclePose(pw.vehicleState()!);
      for (const side of [-1, 1]) maxX = Math.max(maxX, p.x + p.hx * (g.back ?? 2.2) - p.hz * (g.halfWidth ?? 0.9) * side);
      await new Promise(r => setTimeout(r, 0));
    }
    // The front bumper's corners stayed on the ground (a ray every half metre, so within that of the edge).
    expect(maxX).toBeLessThan(104.5);
    expect(trip.state).toBe('arrived');
    expect(trip.progress.turns).toBe(1);
    pw.world.free();
  }, 60_000);

  test('a turn with no room backs up once, then ends the trip stuck', async () => {
    const pw = await devWorld();
    // Ground only on a 7 x 4 m island round the car: every leg reaches the edge at once, backing up too.
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      if (Math.abs(o.x - 100) > 3.5 || Math.abs(o.z - 100) > 2) return null;
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && { ...hit, material: MAT.road };
    };
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 40, z: 100 }, { mode: 'drive', path: [{ x: 100, z: 100 }, { x: 40, z: 100 }], speed: 8 });
    let k = 0;
    for (; k < 60 * 10 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      await new Promise(r => setTimeout(r, 0));
    }
    const v = pw.vehicleState()!;
    expect(trip.state).toBe('failed');
    expect(trip.reason).toBe('stuck: no room to turn');
    expect(trip.progress.turns).toBe(1);
    expect(k).toBeLessThan(60 * 2);
    expect(Math.hypot(v.pos.x - 100, v.pos.z - 100)).toBeLessThan(0.5);
    pw.world.free();
  }, 30_000);

  test('a U-turn in a 5.5 m street runs the wheels up onto the pavement, short of the walls beyond', async () => {
    const pw = await devWorld();
    // Carriageway z 97.25..102.75, pavement 2 m each side, then no ground (building walls in the real streets).
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      const off = Math.abs(o.z - 100);
      if (off > 4.75) return null;
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && { ...hit, material: off > 2.75 ? MAT.sidewalk : MAT.paving_slab };
    };
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 101 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 50, z: 99 }, { mode: 'drive', path: [{ x: 100, z: 101 }, { x: 50, z: 99 }], speed: 8 });
    let maxOff = 0;
    const g = vehicleGeometry('car');
    for (let k = 0; k < 60 * 60 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const p = vehiclePose(pw.vehicleState()!);
      for (const end of [g.back ?? 2.2, -(g.back ?? 2.2)]) for (const side of [-1, 1])
        maxOff = Math.max(maxOff, Math.abs(p.z + p.hz * end + p.hx * (g.halfWidth ?? 0.9) * side - 100));
      await new Promise(r => setTimeout(r, 0));
    }
    expect(trip.state).toBe('arrived');
    expect(trip.progress.turns).toBe(1);
    // No corner of the car went past the pavement (half a metre: the rows' spacing).
    expect(maxOff).toBeLessThan(5.25);
    pw.world.free();
  }, 60_000);

  test('the edge watch: a slow full-lock swing toward a quay edge stops short of it', async () => {
    const pw = await devWorld();
    // Facing east, the path bends 60 degrees right at once (pursuit, not a turn in the street); the ground ends at
    // x = 104.5 (a quay edge): a full-lock swing from rest carries the front corner past it.
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      if (o.x > 104.5) return null;
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && { ...hit, material: MAT.road };
    };
    pw.spawnVehicle({ x: 100, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 103, z: 150 }, { mode: 'drive', path: [{ x: 100, z: 100 }, { x: 102.5, z: 104.3 }, { x: 103, z: 150 }], speed: 8 });
    let maxX = 0;
    const g = vehicleGeometry('car');
    for (let k = 0; k < 60 * 40 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const p = vehiclePose(pw.vehicleState()!);
      for (const side of [-1, 1]) maxX = Math.max(maxX, p.x + p.hx * (g.back ?? 2.2) - p.hz * (g.halfWidth ?? 0.9) * side);
      await new Promise(r => setTimeout(r, 0));
    }
    expect(maxX).toBeLessThan(105);
    expect(trip.state).toBe('arrived');
    pw.world.free();
  }, 60_000);

  test('a lane U-turn at a dead end: the car stops before the hairpin and turns on the carriageway', async () => {
    const pw = await devWorld();
    // East on the lane z = 100, a 1.65 m radius U-turn onto the lane z = 103.3 (a lane graph's dead-end turn between
    // two lanes), back west. The carriageway is z 97.6..105.7; beyond it there is no ground (a quay edge).
    const pts = [];
    for (let x = 40; x <= 110; x += 5) pts.push({ x, z: 100 });
    for (let a = 15; a < 180; a += 15) pts.push({ x: 110 + 1.65 * Math.sin(a * Math.PI / 180), z: 101.65 - 1.65 * Math.cos(a * Math.PI / 180) });
    for (let x = 110; x >= 40; x -= 5) pts.push({ x, z: 103.3 });
    const ray: AutopilotWorld['groundRay'] = async (o, d, max) => {
      if (o.z < 97.6 || o.z > 105.7) return null;
      const hit = await pw.raycast(o, d, max, 'solid');
      return hit && { ...hit, material: MAT.road };
    };
    pw.spawnVehicle({ x: 40, y: G + 0.8, z: 100 }, 0);
    for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    const pilot = pilotOn(pw, ray);
    const trip = pilot.goTo({ x: 40, z: 103.3 }, { mode: 'drive', path: pts, speed: 10 });
    let minZ = Infinity, maxZ = -Infinity;
    for (let k = 0; k < 60 * 60 && trip.state === 'driving'; k++) {
      const a = emptyActions();
      pilot.control(a, 1 / 60);
      pw.fixedStep(null, { throttle: a.throttle, brake: a.brake, steer: a.steer, handbrake: a.handbrake > 0 });
      const v = pw.vehicleState()!;
      minZ = Math.min(minZ, v.pos.z); maxZ = Math.max(maxZ, v.pos.z);
      await new Promise(r => setTimeout(r, 0));
    }
    // The car's middle stayed on the carriageway (pursuit at speed swung it far past the edge).
    expect(minZ).toBeGreaterThan(97.6);
    expect(maxZ).toBeLessThan(105.7);
    expect(trip.state).toBe('arrived');
    expect(trip.progress.turns).toBeGreaterThan(0);
    pw.world.free();
  }, 60_000);
});

describe('reverse-out ground check', () => {
  test('a turn leg, and the swing pursuit steers, stop short of a tree trunk that stands between the ground rays', async () => {
    const { arcRoom, edgeRoom } = await import('../../src/gameplay/autopilot/reverseCheck');
    const g = vehicleGeometry('car');
    const pose = { x: 100, y: 1, z: 100, hx: 1, hz: 0, forwardSpeed: 0 };
    const front = pose.x + (g.back ?? g.rearOffset + 1);
    // Flat road at y 0; a 0.3 m trunk 3 m ahead of the bumper, halfway between its centre and right corner rays.
    const trunk = { x: front + 3, z: pose.z + ((g.halfWidth ?? 0.9) - 0.05) / 2, r: 0.15, top: 4 };
    const world = (withTrunk: boolean): NonNullable<AutopilotWorld['groundRay']> => async (o, d, max) => {
      if (withTrunk) {
        // The trunk as a vertical cylinder: where the ray (a down ray or a level one) meets it first.
        const fx = o.x - trunk.x, fz = o.z - trunk.z, a = d.x * d.x + d.z * d.z;
        if (a < 1e-9 ? Math.hypot(fx, fz) <= trunk.r && o.y > trunk.top : false) {
          return { toi: o.y - trunk.top, point: { x: o.x, y: trunk.top, z: o.z }, normal: { x: 0, y: 1, z: 0 }, material: MAT.tree_trunk, tile: null };
        }
        if (a >= 1e-9) {
          const b = fx * d.x + fz * d.z, c = fx * fx + fz * fz - trunk.r * trunk.r, disc = b * b - a * c;
          const t = disc < 0 ? -1 : (-b - Math.sqrt(disc)) / a;
          if (disc >= 0 && t >= 0 && t <= max && o.y < trunk.top) {
            return { toi: t, point: { x: o.x + d.x * t, y: o.y, z: o.z + d.z * t }, normal: { x: -d.x, y: 0, z: -d.z }, material: MAT.tree_trunk, tile: null };
          }
        }
      }
      if (d.y > -0.5) return null;
      return { toi: o.y, point: { x: o.x, y: 0, z: o.z }, normal: { x: 0, y: 1, z: 0 }, material: MAT.road, tile: null };
    };
    expect(await arcRoom(pose, 0, g, world(false), 1, 6)).toBeCloseTo(6, 5);
    const room = await arcRoom(pose, 0, g, world(true), 1, 6);
    expect(room).toBeGreaterThan(2);
    expect(room).toBeLessThan(3 - trunk.r);
    // At full lock toward a trunk just clear of the car's side by its middle: no bumper row reaches it, the side
    // sweeps into it within a metre.
    Object.assign(trunk, { x: pose.x, z: pose.z + (g.halfWidth ?? 0.9) + 0.25, r: 0.1 });
    expect(await arcRoom(pose, 1, g, world(false), 1, 5)).toBeCloseTo(5, 5);
    expect(await arcRoom(pose, 1, g, world(true), 1, 5)).toBeLessThan(1);
    // Turning away from it, the leg keeps its room.
    expect(await arcRoom(pose, -1, g, world(true), 1, 5)).toBeCloseTo(5, 5);
    // The edge watch's check of pursuit's swing sees it too.
    expect(await edgeRoom(pose, 1, g, world(false), 5)).toBeCloseTo(5, 5);
    expect(await edgeRoom(pose, 1, g, world(true), 5)).toBeLessThan(1);
  });

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

describe('route join', () => {
  test('the route starts from where the car is along it, not from which way the car faces', async () => {
    const { pathAfter } = await import('../../src/gameplay/autopilot/autopilot');
    // Espoo, Itätuulenkuja: the street runs west first, then bends north-west; the car stopped facing north (a park).
    const route = makePath([{ x: 438, z: 499.5 }, { x: 434, z: 500 }, { x: 428, z: 498 }, { x: 418.6, z: 490.8 }, { x: 397, z: 458 }]);
    const after = pathAfter(route, { x: 438.1, z: 499.4 });
    expect(after[0]).toMatchObject({ x: 434, z: 500 });
    // A car already past the first points joins after them.
    expect(pathAfter(route, { x: 427, z: 497 })[0]).toMatchObject({ x: 418.6, z: 490.8 });
  });
});
