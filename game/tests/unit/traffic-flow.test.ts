import { readFileSync } from 'node:fs';
import { describe, expect, test } from 'vitest';
import { CAR, FOOT, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { TrafficFlow, signalState, sampleFlowPose, interpolateFlowPose, unseen, type FlowCar } from '../../src/traffic/flow';
import { TrafficNetwork } from '../../src/traffic/network';
import { trafficRoads } from '../../src/traffic/roads';
import { honking, vehicleLights } from '../../src/traffic/lights';
import { pointAt } from '../../src/traffic/network';
import { LaneChangePlanner, PLAN_BUDGET } from '../../src/traffic/laneChanges';
import { buildTileTraffic, type Lane } from '../../src/traffic/tileNetwork';

export function intersection(signal = false, lanes = 1): RoadGraphJson {
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2,
    bbox: [0, 0, 250, 250], nodes: [125, 125, 0, 125, 250, 125, 125, 0, 125, 250],
    nodeFlags: [signal ? NODE.SIGNALS : 0, NODE.SEAM, NODE.SEAM, NODE.SEAM, NODE.SEAM],
    edges: [1, 2, 3, 4].map(n => [n, 0, CAR | FOOT | 3 << 3, 125]),
    edgeIds: ['west', 'east', 'north', 'south'], bits: { highways: ['secondary'] },
    edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
    edgeAttrs: Array.from({ length: 4 }, () => [lanes, lanes, lanes * 7, 40, 11, 0, 0, 9]),
  };
}

export function flowNetwork(json = intersection()) {
  const net = new TrafficNetwork();
  net.density = { hourAt: () => 16, multiplier: 1 };
  net.setTile(buildTileTraffic('t0_0_0', trafficRoads(json), [0, 0, 250, 250], 'fixture'));
  return net;
}

/** Full separating-axis test on the physical car footprints, with no centre-distance shortcut. */
export function overlap(a: { x: number; z: number; hx: number; hz: number; length: number; width: number },
  b: { x: number; z: number; hx: number; hz: number; length: number; width: number }) {
  for (const [x, z] of [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]) {
    const ra = Math.abs(a.hx * x + a.hz * z) * a.length / 2 + Math.abs(-a.hz * x + a.hx * z) * a.width / 2;
    const rb = Math.abs(b.hx * x + b.hz * z) * b.length / 2 + Math.abs(-b.hz * x + b.hx * z) * b.width / 2;
    if (Math.abs((b.x - a.x) * x + (b.z - a.z) * z) >= ra + rb - 0.01) return false;
  }
  return true;
}

describe('responsive traffic', () => {
  test('parallel lanes stay parallel and city corners have a lateral-acceleration speed limit', () => {
    const tile = [...flowNetwork(intersection(false, 2)).tiles.values()][0];
    const straight = tile.lanes.filter(l => l.connector && Math.abs(l.connector.turn) < 0.5);
    expect(straight.length).toBeGreaterThan(3);
    for (const lane of straight) expect(lane.next!.k).toBe(lane.k);
    const turns = [...flowNetwork().tiles.values()][0].lanes.filter(l => l.connector && Math.abs(l.connector.turn) > 0.5);
    for (const lane of turns) expect(lane.connector!.speed).toBeLessThanOrEqual(7);
  });

  test('mapped controllers use Finnish red-amber preparation and never give crossing approaches green together', () => {
    const tile = [...flowNetwork(intersection(true)).tiles.values()][0];
    const incoming = tile.lanes.filter(l => l.end.node === 0);
    const states = new Set<string>();
    for (let t = 0; t < 60; t += 0.25) {
      for (const lane of incoming) states.add(signalState(lane, t));
      const green = incoming.filter(l => signalState(l, t) === 'green');
      expect(green.length).toBeLessThanOrEqual(2);
    }
    expect(states).toEqual(new Set(['green', 'amber', 'red', 'red-amber']));
  });

  test('a fixed shared epoch produces identical queues regardless of frame cadence', () => {
    const a = new TrafficFlow(flowNetwork(intersection(true))), b = new TrafficFlow(flowNetwork(intersection(true)));
    a.reset(0); b.reset(0);
    for (let t = 0; t <= 90; t += 0.037) a.update(t);
    for (let t = 0; t <= 90; t += 0.183) b.update(t);
    a.update(90); b.update(90);
    const state = (flow: TrafficFlow) => [...flow.cars.values()].map(c => [c.key, c.lane.id, c.s, c.speed]);
    expect(state(a)).toEqual(state(b));
    expect(a.stats.entered).toBeGreaterThan(10);
    expect(a.stats.exited).toBeGreaterThan(5);
  });

  test.each([false, true])('ten simulated minutes have zero body overlaps at a busy intersection (signals=%s)', signal => {
    const flow = new TrafficFlow(flowNetwork(intersection(signal, 2)));
    flow.reset(0);
    let queued = 0, checked = 0, longestWait = 0;
    for (let step = 1; step <= 6000; step++) {
      flow.update(step / 10);
      const cars = [...flow.cars.values()].map(c => ({ ...sampleFlowPose(c, 0, () => 0), length: c.length, width: c.width, key: c.key }));
      for (const car of flow.cars.values()) longestWait = Math.max(longestWait, car.waiting);
      for (let i = 0; i < cars.length; i++) for (let j = i + 1; j < cars.length; j++) {
        if (Math.abs(cars[i].x - cars[j].x) > 6 || Math.abs(cars[i].z - cars[j].z) > 6) continue;
        if (overlap(cars[i], cars[j])) throw new Error(`overlap at ${step / 10}: ${cars[i].key} / ${cars[j].key}`);
        checked++;
      }
      queued += cars.filter(c => c.speed < 0.2).length;
    }
    expect(flow.stats.exited).toBeGreaterThan(50);
    expect(queued).toBeGreaterThan(signal ? 100 : 0);
    expect(checked).toBeGreaterThan(100);
    expect(longestWait).toBeLessThan(90);
  });

  test('junction admission accounts for pending entrants and bounds receiving space by the lane end', () => {
    const flow = new TrafficFlow(flowNetwork(), 0); flow.update(30);
    const car = [...flow.cars.values()].find(c => c.route.connector && c.route.next)!;
    expect(car).toBeDefined();
    car.route.signal = null; car.route.priority = 'normal';
    const next = car.route.next!;
    next.end = { ...next.end, kind: 'junction', seam: null };
    const admission = flow as unknown as {
      byLane: Map<Lane, FlowCar[]>;
      mayEnter(car: FlowCar, heads: Map<Lane, FlowCar>, reservations: FlowCar[], time: number): boolean;
    };
    const heads = new Map([[car.lane, car]]);
    // Keep this regression independent of route choice and unrelated intersection approaches.
    const pending: FlowCar = { ...car, key: 'pending', reservation: car.route, length: 5 };
    next.length = car.length + 9;
    admission.byLane = new Map();
    expect(admission.mayEnter(car, heads, [], 30)).toBe(true);
    expect(admission.mayEnter(car, heads, [pending], 30)).toBe(false);
    expect(car.blockedBy).toBe('storage:pending');
    // A tail beyond the lane end cannot invent room inside a short receiving link.
    next.length = 8;
    admission.byLane.set(next, [{ ...pending, lane: next, s: 30 }]);
    expect(admission.mayEnter(car, heads, [], 30)).toBe(false);
    expect(car.blockedBy).toBe('storage:lane-end');
    admission.byLane.clear();
    expect(admission.mayEnter(car, heads, [], 30)).toBe(false);
    // Once an entrant reaches the destination, the actual tail accounts for it exactly once.
    next.length = 40;
    const arrived = { ...pending, lane: next, s: 20 };
    admission.byLane.set(next, [arrived]);
    expect(admission.mayEnter(car, heads, [arrived], 30)).toBe(true);
    arrived.s = 4;
    expect(admission.mayEnter(car, heads, [arrived], 30)).toBe(false);
    expect(car.blockedBy).toBe('exit:pending');
    admission.byLane.clear();
    next.length = 3; next.end.kind = 'seam'; next.seamNext = null; next.next = null;
    expect(admission.mayEnter(car, heads, [], 30)).toBe(true);
  });

  test.each([29, 70])('a fast lane change with %s metres left cannot strand a car between lanes', remaining => {
    const net = flowNetwork(intersection(false, 2)), flow = new TrafficFlow(net, 0);
    flow.update(20);
    const car = [...flow.cars.values()].find(c => c.lane.laneCount === 2 && c.lane.length > 60)!;
    expect(car).toBeDefined();
    const movements = (flow as unknown as { movements: Map<Lane, Lane[]> }).movements;
    const planner = new LaneChangePlanner([...movements.keys()], movements);
    const target = [...movements.keys()].find(l => l.carriageway === car.lane.carriageway && l.k !== car.lane.k)!;
    const turn = target.k === 0 ? -1 : 1;
    for (let n = 0; n < 1000; n++) {
      car.key = `runway-${n}`;
      if (planner.intent(car.lane, `${car.key}/${car.hops}`) === turn) break;
    }
    expect(planner.intent(car.lane, `${car.key}/${car.hops}`)).toBe(turn);
    car.s = car.lane.length - remaining; car.speed = car.lane.speed;
    car.reservation = null; car.change = null; car.changeCooldown = 0;
    car.signal = { target, since: flow.tick - 20, reason: 'route' };
    car.route = movements.get(car.lane)![0];
    car.previous = { route: car.route, s: car.s, speed: car.speed, change: null, swerve: 0, swerveRate: 0, uturn: null };
    flow.cars.clear(); flow.cars.set(car.key, car); net.density!.multiplier = 0;
    flow.stats.laneChanges = 0;
    flow.update(60);
    expect(car.change).toBeNull();
    expect(car.stopped).toBeLessThan(10);
    expect(flow.stats.laneChanges).toBe(1);
  });

  test('driver indicators blink on the shared clock and brake lamps follow deceleration and stops', () => {
    const flow = new TrafficFlow(flowNetwork(), 0); flow.update(20);
    const car = [...flow.cars.values()].find(c => c.route.connector)!;
    car.change = null; car.signal = null; car.route.connector!.turn = 0.7;
    car.s = car.route.length - 10; car.speed = 5; car.previous.speed = 5;
    expect(vehicleLights(car, 20)).toEqual({ indicator: 1, braking: false });
    expect(vehicleLights(car, 20.4)).toEqual({ indicator: 0, braking: false });
    car.route.connector!.turn = -0.7;
    expect(vehicleLights(car, 20)).toEqual({ indicator: -1, braking: false });
    car.speed = 4;
    expect(vehicleLights(car, 20).braking).toBe(true);
    car.speed = 0; car.previous.speed = 0;
    expect(vehicleLights(car, 20).braking).toBe(true);
    car.s = 0; car.route.connector!.turn = 0;
    expect(vehicleLights(car, 20).indicator).toBe(0);
  });

  test('short-link diamonds produce one receiving movement instead of self-conflicting route copies', () => {
    const json: RoadGraphJson = { ...intersection(),
      nodes: [0,125,100,125,102,124,102,126,104,125,250,125],
      nodeFlags: [NODE.SEAM,NODE.JUNCTION,NODE.JUNCTION,NODE.JUNCTION,NODE.JUNCTION,NODE.SEAM],
      edges: [[0,1,CAR|4|3<<3,100],[1,2,CAR|4|3<<3,Math.sqrt(5)],[1,3,CAR|4|3<<3,Math.sqrt(5)],
        [2,4,CAR|4|3<<3,Math.sqrt(5)],[3,4,CAR|4|3<<3,Math.sqrt(5)],[4,5,CAR|4|3<<3,146]],
      edgeIds: ['in','upper-a','lower-a','upper-b','lower-b','out'],
      edgeAttrs: Array.from({length:6},()=>[1,0,3.5,40,11,0,0,0]),
    };
    const flow = new TrafficFlow(flowNetwork(json), 0); flow.update(45);
    const access = flow as unknown as { movements: Map<Lane,Lane[]>; regroup(): void;
      mayEnter(car: FlowCar, heads: Map<Lane,FlowCar>, reservations: FlowCar[], time: number): boolean };
    const [lane,routes] = [...access.movements].find(([l]) => l.start.kind === 'seam' && l.end.kind === 'junction')!;
    expect(lane).toBeDefined();
    expect(routes).toHaveLength(1);
    const templates = [...flow.cars.values()]; expect(templates.length).toBeGreaterThanOrEqual(2);
    const route = routes[0];
    const driver = (template: FlowCar, s: number, reservation: Lane | null): FlowCar => {
      const speed = reservation ? 4 : 0;
      const car = { ...template, lane, route, s, speed, reservation, hops: 0, waiting: 0, stopped: 0,
        change: null, signal: null, changeCooldown: 0, pose: { ...template.pose },
        previous: { route, s, speed, change: null, swerve: 0, swerveRate: 0, uturn: null } };
      sampleFlowPose(car, 0, () => 0, car.pose); return car;
    };
    const leader = driver(templates[0], lane.length + 2, route);
    const follower = driver(templates[1], lane.length - templates[1].length / 2 - 0.8, null);
    flow.cars.clear(); flow.cars.set(leader.key, leader); flow.cars.set(follower.key, follower); access.regroup();
    expect(access.mayEnter(follower, new Map([[lane,follower]]), [leader], 45)).toBe(true);
    flow.network.density!.multiplier = 0;
    const restored = new TrafficFlow(flowNetwork(json), 0); restored.network.density!.multiplier = 0;
    restored.restore(flow.checkpoint());
    for (let k = 1; k <= 50; k++) {
      flow.update(45 + k / 10); restored.update(45 + k / 10);
      expect(restored.checkpoint()).toEqual(flow.checkpoint());
      const cars = [...flow.cars.values()];
      for (const fraction of [0, 0.5, 1]) for (let i = 0; i < cars.length; i++) for (let j = i + 1; j < cars.length; j++) {
        const physical = (c: FlowCar) => ({ ...interpolateFlowPose(c, fraction, () => 0), length: c.length, width: c.width });
        expect(overlap(physical(cars[i]), physical(cars[j]))).toBe(false);
      }
    }
  });

  test('claims remove simulation participants and interpolation stays between checked positions', () => {
    const flow = new TrafficFlow(flowNetwork(intersection(true)));
    flow.reset(0); flow.update(30);
    const car = [...flow.cars.values()][0];
    const now = sampleFlowPose(car, 0, () => 0);
    const previous = sampleFlowPose({ ...car, ...car.previous }, 0, () => 0);
    for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
      const pose = interpolateFlowPose(car, fraction, () => 0);
      expect(pose.x).toBeCloseTo(previous.x + (now.x - previous.x) * fraction, 8);
      expect(pose.z).toBeCloseTo(previous.z + (now.z - previous.z) * fraction, 8);
    }
    flow.suppress(car.key);
    expect(flow.cars.has(car.key)).toBe(false);
    flow.reset(0); flow.update(30);
    expect(flow.cars.has(car.key)).toBe(false);
  });
});

describe('drivers and players', () => {
  const blocked = () => {
    const flow = new TrafficFlow(flowNetwork(), 0);
    flow.reset(0);
    const lane = [...flowNetwork().tiles.values()][0].lanes.find(l => l.start.kind === 'seam' && l.length > 100)!;
    const at = pointAt(lane.points, lane.cum, 70);
    const player = { id: 'local', x: at.x, y: 0, z: at.z, hx: at.hx, hz: at.hz, vx: 0, vz: 0, length: 4.2, width: 1.8 };
    return { flow, lane, player };
  };

  test('drivers stop behind a player car in the lane without contact, honk, then go round it and return', () => {
    const { flow, lane, player } = blocked();
    let honked = 0, held = 0, passed = new Set<string>(), swerved = 0;
    for (let step = 1; step <= 1800; step++) {
      flow.setPlayers([], [player], step / 10);
      flow.update(step / 10);
      const cars = [...flow.cars.values()].map(c => ({ ...sampleFlowPose(c, 0, () => 0), length: c.length, width: c.width, car: c }));
      for (const [i, a] of cars.entries()) {
        if (overlap(a, player)) throw new Error(`${a.car.key} drove into the player at ${step / 10}`);
        for (const b of cars.slice(i + 1)) if (overlap(a, b)) throw new Error(`${a.car.key} / ${b.car.key} overlap at ${step / 10}`);
        if (a.car.lane.id === lane.id && a.car.blockedBy === 'player') held++;
        if (honking(a.car)) honked++;
        if (a.car.swerve) swerved++;
        if (a.car.lane.id === lane.id && a.car.s > 80) passed.add(a.car.key);
        // Back on the lane centre before the junction.
        if (a.car.lane.id === lane.id && a.car.s > lane.length - 2) expect(Math.abs(a.car.swerve)).toBeLessThan(0.05);
      }
    }
    expect(held).toBeGreaterThan(20);
    expect(honked).toBeGreaterThan(0);
    expect(swerved).toBeGreaterThan(0);
    expect(passed.size).toBeGreaterThan(3);
  });

  test('a queue held by a player car moves up once the player drives off', () => {
    const { flow, lane, player } = blocked();
    for (let step = 1; step <= 300; step++) { flow.setPlayers([], [player], step / 10); flow.update(step / 10); }
    const queued = [...flow.cars.values()].filter(c => c.lane.id === lane.id && c.s < 70 && !c.swerveTarget).map(c => ({ key: c.key, s: c.s }));
    for (let step = 301; step <= 600; step++) { flow.setPlayers([], [], step / 10); flow.update(step / 10); }
    expect([...flow.cars.values()].some(honking)).toBe(false);
    for (const { key, s } of queued) {
      const now = flow.cars.get(key);
      expect(!now || now.lane.id !== lane.id || now.s > s + 10).toBe(true);
    }
  });
});

describe('aggressive drivers: lane changes, reverses and new routes when blocked', () => {
  const run = (lanes: number, obstacle: (net: TrafficNetwork) => { lane: Lane; body: Parameters<TrafficFlow['setPlayers']>[1][number] }, seconds = 240) => {
    const net = flowNetwork(intersection(false, lanes)), flow = new TrafficFlow(net, 0);
    flow.reset(0);
    const { lane, body } = obstacle(net);
    let reversed = 0;
    const last = new Map<string, { lane: Lane; s: number }>();
    for (let step = 1; step <= seconds * 10; step++) {
      flow.setPlayers([], [body], step / 10); flow.update(step / 10);
      const cars = [...flow.cars.values()].map(c => ({ ...sampleFlowPose(c, 0, () => 0), length: c.length, width: c.width, car: c }));
      for (const [i, a] of cars.entries()) {
        if (overlap(a, body)) throw new Error(`${a.car.key} drove into the obstacle at ${step / 10}`);
        for (const b of cars.slice(i + 1)) if (overlap(a, b)) throw new Error(`${a.car.key} / ${b.car.key} overlap at ${step / 10}`);
        const before = last.get(a.car.key);
        if (before && before.lane === a.car.lane && a.car.s < before.s - 0.01) reversed++;
        last.set(a.car.key, { lane: a.car.lane, s: a.car.s });
      }
    }
    return { flow, lane, reversed };
  };
  const parked = (lanes: number) => (net: TrafficNetwork) => {
    const lane = [...net.tiles.values()][0].lanes.find(l => l.start.kind === 'seam' && l.length > 100 && l.k === 0 && l.laneCount === lanes)!;
    const at = pointAt(lane.points, lane.cum, 60);
    return { lane, body: { id: 'parked', x: at.x, y: 0, z: at.z, hx: at.hx, hz: at.hz, vx: 0, vz: 0, length: 4.5, width: 1.9 } };
  };

  test('on a two-lane road, drivers stopped behind a parked car get past it in the free lane and nobody locks', () => {
    const { flow, lane } = run(2, parked(2));
    expect(flow.stats.escapes + flow.stats.laneChanges).toBeGreaterThan(2);
    expect([...flow.cars.values()].filter(c => c.stuck > 60).map(c => `${c.key} by ${c.blockedBy}`)).toEqual([]);
    expect(flow.stats.exited).toBeGreaterThan(40);
    void lane;
  });

  test('on a one-lane road, a driver nose up against the parked car backs up a little before pulling out round it', () => {
    const { flow, reversed } = run(1, parked(1));
    expect(flow.stats.reverses).toBeGreaterThan(0);
    expect(flow.stats.reverses).toBeLessThan(80); // once per driver that needs it, not every tick
    expect(reversed).toBeGreaterThan(0);
  });

  test('a truck across a two-way street: the front car backs off, then turns round onto the other lane', () => {
    const { flow, reversed } = run(1, net => {
      const lane = [...net.tiles.values()][0].lanes.find(l => l.start.kind === 'seam' && l.length > 100)!;
      const at = pointAt(lane.points, lane.cum, 70);
      return { lane, body: { id: 'truck', x: at.x, y: 0, z: at.z, hx: -at.hz, hz: at.hx, vx: 0, vz: 0, length: 16, width: 2.6 } };
    }, 400);
    expect(flow.stats.reverses).toBeGreaterThan(0);
    expect(flow.stats.uturns).toBeGreaterThan(0);
    // It rocks while it waits (many separate backward moves, not one).
    expect(reversed).toBeGreaterThan(25);
  });

  test('a moving driver goes round a parked car without stopping first when the corridor is clear', () => {
    const net = flowNetwork(intersection(false, 1)), flow = new TrafficFlow(net, 0);
    flow.reset(0);
    const { lane, body } = parked(1)(net);
    let passedMoving = 0;
    for (let step = 1; step <= 1200; step++) {
      flow.setPlayers([], [body], step / 10); flow.update(step / 10);
      for (const c of flow.cars.values()) if (c.lane === lane && c.swerveTarget && c.speed > 2 && c.held === 0) passedMoving++;
    }
    expect(passedMoving).toBeGreaterThan(0);
  });

  test('a junction head kept at its line by a blocked exit takes another way', () => {
    const { flow } = run(1, net => {
      // A truck across the street the approach would go straight into, just past the junction.
      const tile = [...net.tiles.values()][0];
      const exit = tile.lanes.find(l => l.start.kind === 'junction' && l.length > 100)!;
      const at = pointAt(exit.points, exit.cum, 14);
      return { lane: exit, body: { id: 'truck', x: at.x, y: 0, z: at.z, hx: -at.hz, hz: at.hx, vx: 0, vz: 0, length: 16, width: 2.5 } };
    });
    expect(flow.stats.rerouted).toBeGreaterThan(0);
    // Some turn off before reaching the line (GTA: at the last junction, without stopping).
    expect(flow.stats.reroutedAhead).toBeGreaterThan(0);
  });
});

describe('out-of-view recycling (GTA-style)', () => {
  // A player's truck across the whole carriageway: nobody can go round it, so the queue behind it locks.
  const locked = (watchers: Parameters<TrafficFlow['setWatchers']>[0]) => {
    const net = flowNetwork(), flow = new TrafficFlow(net, 0);
    flow.reset(0);
    const lane = [...net.tiles.values()][0].lanes.find(l => l.start.kind === 'seam' && l.length > 100)!;
    const at = pointAt(lane.points, lane.cum, 70);
    const truck = { id: 'truck', x: at.x, y: 0, z: at.z, hx: -at.hz, hz: at.hx, vx: 0, vz: 0, length: 16, width: 2.5 };
    flow.setWatchers(watchers);
    for (let step = 1; step <= 4000; step++) { flow.setPlayers([], [truck], step / 10); flow.update(step / 10); }
    const queue = [...flow.cars.values()].filter(c => c.lane.id === lane.id && c.s < 70);
    return { flow, queue, at };
  };

  test('without a watcher list nothing is recycled: front cars turn round instead', () => {
    const { flow } = locked(null);
    expect(flow.stats.recycled).toBe(0);
    expect(flow.stats.uturns).toBeGreaterThan(0);
  });

  test('a camera looking at the queue keeps every car; one looking away from 150 m lets the locked ones go', () => {
    const { at } = locked(null);
    const watching = locked([{ x: at.x - 60 * at.hx, z: at.z - 60 * at.hz, fx: at.hx, fz: at.hz, halfFov: 0.6 }]);
    expect(watching.flow.stats.recycled).toBe(0);
    const away = locked([{ x: at.x + 150 * at.hx, z: at.z + 150 * at.hz, fx: at.hx, fz: at.hz, halfFov: 0.6 }]);
    expect(away.flow.stats.recycled).toBeGreaterThan(0);
    expect(away.queue.every(c => c.stuck < 200)).toBe(true);
    for (const r of away.flow.recycledLog) expect(unseen([{ x: at.x + 150 * at.hx, z: at.z + 150 * at.hz, fx: at.hx, fz: at.hz, halfFov: 0.6 }], r.x, r.z)).toBe(true);
  });

  test('multiplayer: peers given the same stamped watch recycle the same cars; a missed watch recycles nothing', () => {
    const run = (schedule: boolean) => {
      const net = flowNetwork(), flow = new TrafficFlow(net, 0);
      flow.reset(0);
      const lane = [...net.tiles.values()][0].lanes.find(l => l.start.kind === 'seam' && l.length > 100)!;
      const at = pointAt(lane.points, lane.cum, 70);
      const truck = { id: 'truck', x: at.x, y: 0, z: at.z, hx: -at.hz, hz: at.hx, vx: 0, vz: 0, length: 16, width: 2.5 };
      for (let step = 1; step <= 4000; step++) {
        // A watch every second: the player stands 400 m away (sees nothing here).
        if (schedule && step % 10 === 0) flow.scheduleWatch(step + 20, step + 35, [{ x: at.x + 400, z: at.z }]);
        flow.setPlayers([], [truck], step / 10); flow.update(step / 10);
      }
      return flow;
    };
    const a = run(true), b = run(true);
    expect(a.stats.recycled).toBeGreaterThan(0);
    expect(a.recycledLog.map(r => r.key)).toEqual(b.recycledLog.map(r => r.key));
    expect(JSON.stringify(a.checkpoint())).toBe(JSON.stringify(b.checkpoint()));
    const missed = new TrafficFlow(flowNetwork(), 0);
    missed.scheduleWatch(0, 1, []);
    for (let step = 1; step <= 2000; step++) missed.update(step / 10);
    expect(missed.stats.recycled).toBe(0);
  });

  test('hidden jams go sooner far from every watcher (GTA: seconds beyond 150 m)', () => {
    const { at } = locked(null);
    const near = locked([{ x: at.x - 120 * at.hx, z: at.z - 120 * at.hz, fx: -at.hx, fz: -at.hz, halfFov: 0.6 }]);
    const far = locked([{ x: at.x - 400 * at.hx, z: at.z - 400 * at.hz, fx: -at.hx, fz: -at.hz, halfFov: 0.6 }]);
    expect(far.flow.stats.recycled).toBeGreaterThan(near.flow.stats.recycled);
  });

  test('nobody watching (a headless area): locked cars are recycled', () => {
    const { flow, queue } = locked([]);
    expect(flow.stats.recycled).toBeGreaterThan(0);
    expect(queue.every(c => c.stuck < 200)).toBe(true);
  });

  test('a queue at a red light is not a jam: a busy signalled junction nobody watches keeps its waiting cars', () => {
    const flow = new TrafficFlow(flowNetwork(intersection(true, 2)));
    flow.reset(0); flow.setWatchers([]);
    let redWaits = 0;
    for (let step = 1; step <= 3000; step++) {
      flow.update(step / 10);
      for (const c of flow.cars.values()) if (c.stuck > 20 && c.waiting > 20 && signalState(c.route, step / 10) !== 'green') redWaits++;
    }
    expect(redWaits).toBeGreaterThan(100);
    expect(flow.stats.recycled).toBe(0);
  });
});

describe('dead ends (GTA: wander never enters one)', () => {
  // A through street west-east with a 65 m cul-de-sac to the north of its junction.
  const tee = (spur: number): RoadGraphJson => ({ ...intersection(),
    nodes: [125, 125, 0, 125, 250, 125, 125, 125 - spur], nodeFlags: [0, NODE.SEAM, NODE.SEAM, NODE.DEAD_END],
    edges: [[1, 0, CAR | FOOT | 3 << 3, 125], [0, 2, CAR | FOOT | 3 << 3, 125], [0, 3, CAR | FOOT | 3 << 3, spur]],
    edgeIds: ['west', 'east', 'spur'], edgeAttrs: Array.from({ length: 3 }, () => [1, 1, 7, 40, 11, 0, 0, 9]) });
  const entering = (spur: number) => {
    const net = flowNetwork(tee(spur)), flow = new TrafficFlow(net, 0);
    flow.reset(0);
    const lanes = [...net.tiles.values()][0].lanes, into = lanes.filter(l => l.end.kind === 'junction' && l.end.node === 3);
    expect(into.length).toBeGreaterThan(0);
    // Cars that turn in (cars the start population placed on the spur only drive out).
    const was = new Map<string, Lane>(), seen = new Set<string>();
    for (let step = 1; step <= 3000; step++) {
      flow.update(step / 10);
      for (const c of flow.cars.values()) {
        const before = was.get(c.key);
        if (before && before !== c.lane && into.includes(c.lane)) seen.add(c.key);
        was.set(c.key, c.lane);
      }
    }
    return { seen: seen.size, exited: flow.stats.exited };
  };

  test('drivers on the through street never turn into a cul-de-sac', () => {
    const { seen, exited } = entering(65);
    expect(exited).toBeGreaterThan(20);
    expect(seen).toBe(0);
  });

  test('a long dead-end road (it leads somewhere) keeps its traffic', () => {
    expect(entering(110 + 200).seen).toBeGreaterThan(0);
  });
});

describe('junction stop lines', () => {
  test('a car waiting at a line never stands in a crossing movement\'s path through the junction', () => {
    const net = flowNetwork(intersection(false, 2)), flow = new TrafficFlow(net, 0);
    flow.update(60);
    const x = flow as unknown as { movements: Map<Lane, Lane[]>; safeFront(l: Lane): number };
    for (const lane of x.movements.keys()) {
      if (lane.end.kind !== 'junction') continue;
      const front = x.safeFront(lane);
      expect(front).toBeLessThanOrEqual(lane.length - 0.8);
      expect(front).toBeGreaterThanOrEqual(lane.length - 0.8 - 6);
    }
  });
});

describe('incremental graph rebuilds', () => {
  const signature = (f: TrafficFlow) => {
    const x = f as unknown as { movements: Map<Lane, Lane[]> };
    const m = [...x.movements].map(([l, rs]) => `${l.tile}:${l.id}=` + rs.map(r => `${r.id}/${r.connector?.length.toFixed(6)}`).join(',')).sort().join('|');
    const c = [...f.allConflicts()].map(([a, set]) => `${a.tile}:${a.id}>` + [...set].map(b => `${b.tile}:${b.id}`).join(',')).sort().join('|');
    return `${m}#${c}`;
  };
  test('installing and releasing tiles keeps movements and conflicts equal to a fresh build, with cars kept', () => {
    const fixture = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as { a: RoadGraphJson; b: RoadGraphJson };
    const shifted = (json: RoadGraphJson, dx: number, dz: number): RoadGraphJson => ({ ...json, bbox: [json.bbox[0] + dx, json.bbox[1] + dz, json.bbox[2] + dx, json.bbox[3] + dz],
      nodes: json.nodes.map((v, i) => v + (i % 2 ? dz : dx)), edges: json.edges.map(row => row.map((v, i) => i < 4 ? v : v + (i % 2 ? dz : dx))) });
    const tile = (id: string, json: RoadGraphJson) => buildTileTraffic(id, trafficRoads(json), json.bbox as [number, number, number, number], id);
    const tiles = [tile('t0_0_0', fixture.a), tile('t0_1_0', fixture.b), tile('t0_0_1', shifted(fixture.a, 0, 250)), tile('t0_1_1', shifted(fixture.b, 0, 250)),
      tile('t0_0_0', intersection(true, 2))];
    const net = new TrafficNetwork(); net.density = { hourAt: () => 16, multiplier: 1 };
    net.setTile(tiles[0]);
    const flow = new TrafficFlow(net, 0); flow.reset(0);
    let t = 0;
    const changes: (() => void)[] = [() => net.setTile(tiles[1]), () => net.setTile(tiles[2]), () => net.setTile(tiles[3]),
      () => net.removeTile('t0_1_0'), () => net.setTile(tiles[1]), () => net.setTile(tiles[4]), () => net.removeTile('t0_0_1')];
    for (const change of changes) {
      for (let k = 0; k < 100; k++) flow.update(t += 0.1);
      const before = flow.cars.size;
      change(); flow.update(t += 0.1);
      const fresh = new TrafficFlow(net, 0); fresh.reset(t);
      expect(signature(flow)).toBe(signature(fresh));
      if (before) expect(flow.cars.size).toBeGreaterThan(0);
    }
  });

  test('a budgeted rebuild runs in slices beside the live graph and ends equal to a fresh build', () => {
    const fixture = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as { a: RoadGraphJson; b: RoadGraphJson };
    const tile = (id: string, json: RoadGraphJson) => buildTileTraffic(id, trafficRoads(json), json.bbox as [number, number, number, number], id);
    const net = new TrafficNetwork(); net.density = { hourAt: () => 16, multiplier: 1 };
    net.setTile(tile('t0_0_0', fixture.a));
    const flow = new TrafficFlow(net, 0); flow.reset(0); flow.rebuildBudgetMs = 0;
    let t = 0;
    for (let k = 0; k < 600; k++) flow.update(t += 0.1);
    const before = signature(flow), cars = flow.cars.size;
    net.setTile(tile('t0_1_0', fixture.b));
    flow.update(t += 0.1);
    expect(signature(flow)).toBe(before); // one slice: still the live graph, cars still driving
    expect(flow.cars.size).toBe(cars);
    let updates = 1;
    for (; updates < 10_000 && signature(flow) === before; updates++) flow.update(t += 0.1);
    expect(updates).toBeGreaterThan(2);
    const fresh = new TrafficFlow(net, 0); fresh.reset(t);
    expect(signature(flow)).toBe(signature(fresh));
    if (cars) expect(flow.cars.size).toBeGreaterThan(0);
  });
});

describe('simulation budgets', () => {
  test('lane-change planning never exceeds its per-tick budget, even with every car due at once', () => {
    const net = flowNetwork(intersection(false, 2)), flow = new TrafficFlow(net, 0);
    flow.update(30);
    const movements = (flow as unknown as { movements: Map<Lane, Lane[]> }).movements;
    const planner = new LaneChangePlanner([...movements.keys()], movements);
    const byLane = new Map<Lane, FlowCar[]>();
    const template = [...flow.cars.values()].find(c => c.lane.laneCount === 2 && c.lane.length > 60)!;
    const cars: FlowCar[] = Array.from({ length: PLAN_BUDGET * 3 }, (_, i) => ({ ...template, key: `budget:${i}`, s: 20, speed: 2,
      change: null, reservation: null, changeCooldown: 0, signal: { target: template.lane, since: 0, reason: 'queue' } }));
    let most = 0;
    for (let tick = 100; tick < 110; tick++) {
      for (const car of cars) planner.target(car, tick, byLane);
      expect(planner.planned).toBeLessThanOrEqual(PLAN_BUDGET);
      most = Math.max(most, planner.planned);
    }
    expect(most).toBe(PLAN_BUDGET); // the first tick had every signalling car due
  });
});

describe('lane geometry', () => {
  test('a 0.1 m-quantised diagonal street gives smooth lanes: no reversals or sharp vertices, arc length = distance', () => {
    // Generated data rounds to 0.1 m: a diagonal arrives as a staircase of 0.1 m axis steps.
    const interior: number[] = [];
    let x = 20, z = 20;
    while (x < 200) {
      const tx = Math.round((x + 0.1) * 10) / 10, tz = Math.round((20 + (tx - 20) * 0.66) * 10) / 10;
      interior.push(tx, z); if (tz !== z) interior.push(tx, tz);
      x = tx; z = tz;
    }
    const nodes = [20, 20, interior.at(-2)!, interior.at(-1)!];
    const json: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, 250, 250],
      nodes, edges: [[0, 1, CAR | FOOT | 2 << 3, 0, ...interior.slice(0, -2)]] };
    const lanes = buildTileTraffic('t0_0_0', trafficRoads(json), [0, 0, 250, 250] as const, 'stairs').lanes;
    expect(lanes.length).toBe(2);
    for (const lane of lanes) {
      const p = lane.points, n = p.length / 2;
      for (let i = 1; i < n - 1; i++) {
        const a = Math.atan2(p[2 * i + 1] - p[2 * i - 1], p[2 * i] - p[2 * i - 2]), b = Math.atan2(p[2 * i + 3] - p[2 * i + 1], p[2 * i + 2] - p[2 * i]);
        expect(Math.abs(Math.atan2(Math.sin(b - a), Math.cos(b - a)))).toBeLessThan(0.1);
      }
      expect(lane.length / Math.hypot(p[2 * n - 2] - p[0], p[2 * n - 1] - p[1])).toBeLessThan(1.002);
    }
  });
});
