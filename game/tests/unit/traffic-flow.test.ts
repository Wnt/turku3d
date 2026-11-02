import { describe, expect, test } from 'vitest';
import { CAR, FOOT, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { TrafficFlow, signalState, sampleFlowPose, interpolateFlowPose } from '../../src/traffic/flow';
import { TrafficNetwork } from '../../src/traffic/network';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic } from '../../src/traffic/tileNetwork';

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
