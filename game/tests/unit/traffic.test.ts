import { describe, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import * as R from '@dimforge/rapier3d';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
// @ts-expect-error r186 internal pipeline manager has no DefinitelyTyped declaration.
import Pipelines from 'three/src/renderers/common/Pipelines.js';
import { CAR, FOOT, ONEWAY, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { tileBounds } from '../../src/render/lights/lampRule';
import { RING, TRAFFIC, TRAFFIC_MODELS } from '../../src/traffic/constants';
import { HOURLY, profile, slotShare, type TrafficDensity } from '../../src/traffic/density';
import { cycleHour } from '../../src/core/dayCycle';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic, laneSpan, type Lane } from '../../src/traffic/tileNetwork';
import { TrafficNetwork, blankPose } from '../../src/traffic/network';
import { ClaimSet } from '../../src/traffic/claims';
import { enabledTiles, roadHash } from '../../src/traffic/gate';
import { inView, mayCreate, mustRemove } from '../../src/traffic/ring';
import { TrafficSim, TRAFFIC_STRIDE, type TrafficHost } from '../../src/physics/traffic';
import { ParkedCarVisuals } from '../../src/actors/car';
import { instancePaintOf, paintOf } from '../../src/actors/parkedPaint';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { draftManifest } from '../../src/world/manifest';
import { RoadAgreement } from '../../../server/roads.js';

const STREET = CAR | FOOT | 2 << 3, MAJOR_ONEWAY = CAR | FOOT | ONEWAY | 3 << 3;
const H = TRAFFIC.headway;
const T0 = 1.0e7 + 0.123;

/** Axis-aligned streets over x 0..500, z 0..250 (tiles t0_0_0 and t0_1_0), split at every crossing and at the seam
 *  x = 250 as the pipeline clips roads.json per tile. `extra` adds streets to one tile only (a newer version). */
function city(extra: { tile: string; ways: { from: [number, number]; to: [number, number]; flags: number }[] } | null = null) {
  const streets: { from: [number, number]; to: [number, number]; flags: number }[] = [
    { from: [0, 100], to: [500, 100], flags: STREET },
    { from: [30, 180], to: [470, 180], flags: STREET },
    { from: [480, 40], to: [20, 40], flags: MAJOR_ONEWAY },
    { from: [80, 0], to: [80, 250], flags: STREET },
    { from: [170, 40], to: [170, 230], flags: STREET },
    { from: [330, 0], to: [330, 250], flags: STREET },
    { from: [420, 20], to: [420, 240], flags: STREET },
  ];
  const tiles = new Map<string, { nodes: number[]; edges: number[][] }>();
  const add = (tile: string, a: [number, number], b: [number, number], flags: number) => {
    const t = tiles.get(tile) ?? { nodes: [], edges: [] };
    tiles.set(tile, t);
    const node = (p: [number, number]) => {
      for (let i = 0; i < t.nodes.length; i += 2) if (Math.hypot(t.nodes[i] - p[0], t.nodes[i + 1] - p[1]) < 0.05) return i / 2;
      t.nodes.push(p[0], p[1]); return t.nodes.length / 2 - 1;
    };
    t.edges.push([node(a), node(b), flags, Math.hypot(b[0] - a[0], b[1] - a[1])]);
  };
  const cut = (s: { from: [number, number]; to: [number, number]; flags: number }, all: typeof streets) => {
    const [x0, z0] = s.from, [x1, z1] = s.to, horizontal = z0 === z1;
    const stops = new Set<number>([horizontal ? x0 : z0, horizontal ? x1 : z1]);
    for (const o of all) {
      if (o === s || (o.from[1] === o.to[1]) === horizontal) continue;
      const c = horizontal ? o.from[0] : o.from[1];
      const [lo, hi] = horizontal ? [Math.min(o.from[1], o.to[1]), Math.max(o.from[1], o.to[1])] : [Math.min(o.from[0], o.to[0]), Math.max(o.from[0], o.to[0])];
      const along = horizontal ? z0 : x0;
      if (along >= lo && along <= hi && c > Math.min(horizontal ? x0 : z0, horizontal ? x1 : z1) && c < Math.max(horizontal ? x0 : z0, horizontal ? x1 : z1)) stops.add(c);
    }
    if (horizontal && Math.min(x0, x1) < 250 && Math.max(x0, x1) > 250) stops.add(250);
    const sorted = [...stops].sort((a, b) => a - b);
    if ((horizontal ? x1 < x0 : z1 < z0)) sorted.reverse();
    const point = (v: number): [number, number] => horizontal ? [v, z0] : [x0, v];
    for (let i = 1; i < sorted.length; i++) {
      const a = point(sorted[i - 1]), b = point(sorted[i]);
      add(`t0_${Math.floor((a[0] + b[0]) / 2 / 250)}_0`, a, b, s.flags);
    }
  };
  for (const s of streets) cut(s, streets);
  for (const s of extra?.ways ?? []) add(extra!.tile, s.from, s.to, s.flags);
  const json = new Map<string, RoadGraphJson>();
  for (const [tile, t] of tiles) json.set(tile, { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    bbox: [...tileBounds(tile)!] as [number, number, number, number], nodes: t.nodes, edges: t.edges });
  return json;
}

function network(tiles: Map<string, RoadGraphJson>, order = [...tiles.keys()]) {
  const net = new TrafficNetwork();
  // Each client parses its own copy of the data.
  for (const id of order) net.setTile(buildTileTraffic(id, trafficRoads(JSON.parse(JSON.stringify(tiles.get(id)))), tileBounds(id)!, roadHash(tiles.get(id)!)));
  return net;
}

/** Every car of the network at time t: key → smoothed pose (flat ground). */
function cars(net: TrafficNetwork, t: number, claims?: ClaimSet) {
  const out = new Map<string, number[]>();
  claims?.follow(t);
  net.enumerate(250, 125, 400, t, (lane, n) => {
    const key = net.key(lane, n);
    if (claims?.has(key)) return;
    const p = net.pose(lane, n, t, () => 0, blankPose());
    out.set(key, [p.x, p.z, p.hx, p.hz, p.speed]);
  });
  return out;
}

describe('deterministic conveyor traffic', () => {
  test('two clients with the same clock and tiles compute identical cars, whatever their load order', () => {
    const tiles = city();
    const a = network(tiles, ['t0_0_0', 't0_1_0']), b = network(tiles, ['t0_1_0', 't0_0_0']);
    let total = 0;
    for (let k = 0; k < 240; k++) {
      const t = T0 + k * 0.37;
      const ca = cars(a, t), cb = cars(b, t);
      expect([...ca.keys()].sort()).toEqual([...cb.keys()].sort());
      for (const [key, pose] of ca) expect(cb.get(key)).toEqual(pose);
      total += ca.size;
    }
    expect(total / 240).toBeGreaterThan(20);
  });

  test('cars move continuously through junctions and across the seam, and never close up on their chain', () => {
    const net = network(city());
    const lanes = [...net.tiles.values()].flatMap(t => t.lanes);
    expect(lanes.some(l => l.seamNext)).toBe(true);
    // Follow every car near the centre for 90 s at 60 Hz.
    const start: { lane: Lane; n: number }[] = [];
    net.enumerate(250, 125, 300, T0, (lane, n) => start.push({ lane, n }));
    let seams = 0, junctions = 0;
    for (const car of start) {
      let at: { lane: Lane; n: number } | null = car, last: number[] | null = null;
      for (let k = 0; k <= 90 * 60 && at; k++) {
        const t = T0 + k / 60;
        const next = net.advance(at.lane, at.n, t);
        if (!next) break;
        if (next.lane !== at.lane) { if (next.lane.tile !== at.lane.tile) seams++; else junctions++; }
        at = next;
        const p = net.pose(at.lane, at.n, t, () => 0);
        if (last) expect(Math.hypot(p.x - last[0], p.z - last[1])).toBeLessThan(at.lane.speed / 60 * 1.6 + 0.02);
        last = [p.x, p.z];
      }
    }
    expect(seams).toBeGreaterThan(5);
    expect(junctions).toBeGreaterThan(50);
    // Consecutive slots of one lane stay a headway apart: never closer than a car length plus a gap.
    for (let k = 0; k < 120; k++) {
      const t = T0 + k * 0.5, byLane = new Map<Lane, number[][]>();
      net.enumerate(250, 125, 400, t, (lane, n) => {
        const p = net.pose(lane, n, t, () => 0);
        byLane.set(lane, [...byLane.get(lane) ?? [], [p.x, p.z]]);
      });
      for (const list of byLane.values()) for (let i = 1; i < list.length; i++) {
        expect(Math.hypot(list[i][0] - list[i - 1][0], list[i][1] - list[i - 1][1])).toBeGreaterThan(7);
      }
    }
  });

  test('phases lock: each matched lane hands its slot to the next lane exactly on that lane\'s grid', () => {
    const net = network(city());
    for (const tile of net.tiles.values()) for (const lane of tile.lanes) {
      if (!lane.next) continue;
      const exit = lane.phase + laneSpan(lane);
      const slots = (exit - lane.next.phase) / H;
      expect(Math.abs(slots - Math.round(slots))).toBeLessThan(1e-6);
      expect(lane.wait).toBeGreaterThanOrEqual(0);
      expect(lane.wait).toBeLessThan(H);
    }
  });

  test('two clients with different roads.json for one tile spawn nothing there, and agree elsewhere', () => {
    const v1 = city(), v2 = city({ tile: 't0_0_0', ways: [{ from: [200, 230], to: [240, 230], flags: STREET }] });
    const hashes = (tiles: Map<string, RoadGraphJson>) => new Map([...tiles].map(([id, json]) => [id, roadHash(json)]));
    const localA = hashes(v1), localB = hashes(v2);
    expect(localA.get('t0_0_0')).not.toBe(localB.get('t0_0_0'));
    expect(localA.get('t0_1_0')).toBe(localB.get('t0_1_0'));
    // The real server rule (server/roads.js) between two connected players.
    const sent: Record<string, unknown>[] = [];
    const players = [{ online: true, connection: {} as object, roads: null }, { online: true, connection: {} as object, roads: null }];
    const room = { players: new Map(players.map((p, i) => [i, p])), connections: new Set(players.map(p => ({ player: p }))),
      send: (_c: unknown, type: string, fields: Record<string, unknown>) => sent.push({ type, ...fields }) };
    const roads = new RoadAgreement(room);
    roads.report(players[0], Object.fromEntries(localA));
    roads.report(players[1], Object.fromEntries(localB));
    const agreed = new Map(Object.entries((sent.at(-1) as { agreed: Record<string, string> }).agreed));
    expect([...agreed.keys()]).toEqual(['t0_1_0']);
    const enabledA = enabledTiles(localA, agreed, true), enabledB = enabledTiles(localB, agreed, true);
    expect(enabledA).toEqual(['t0_1_0']);
    expect(enabledB).toEqual(['t0_1_0']);
    const a = network(v1), b = network(v2);
    a.setEnabled(new Set(enabledA)); b.setEnabled(new Set(enabledB));
    for (let k = 0; k < 60; k++) {
      const t = T0 + k * 1.3, ca = cars(a, t), cb = cars(b, t);
      expect([...ca.keys()].filter(key => key.startsWith('t0_0_0'))).toEqual([]);
      expect([...cb.keys()].filter(key => key.startsWith('t0_0_0'))).toEqual([]);
      expect([...ca.keys()].sort()).toEqual([...cb.keys()].sort());
      for (const [key, pose] of ca) expect(cb.get(key)).toEqual(pose);
    }
    // Before the server answers, a multiplayer client spawns nothing; single player spawns everywhere.
    expect(enabledTiles(localA, null, true)).toEqual([]);
    expect(enabledTiles(localA, null, false)).toBeNull();
  });

  test('a claimed slot disappears on both clients, also after it crosses into the next tile', () => {
    const tiles = city();
    const a = network(tiles), b = network(tiles);
    const claimsA = new ClaimSet(a), claimsB = new ClaimSet(b);
    // Client A claims an eastbound car on the street that crosses the seam, shortly before the seam.
    let claimed: { lane: Lane; n: number } | null = null;
    a.enumerate(250, 125, 400, T0, (lane, n) => {
      const p = a.pose(lane, n, T0, () => 0);
      if (!claimed && lane.tile === 't0_0_0' && lane.end.seam && p.hx > 0.9 && p.x > 170 && p.x < 240) claimed = { lane, n };
    });
    expect(claimed).not.toBeNull();
    const key = a.key(claimed!.lane, claimed!.n);
    claimsA.addLocal(key);
    // The registry relays it as source `tr:<key>`; client B receives it as a remote claim.
    claimsB.setRemote([key]);
    const before = cars(a, T0);
    expect(before.has(key)).toBe(true);
    const later = T0 + 30;
    const continued = a.advance(claimed!.lane, claimed!.n, later)!;
    expect(continued.lane.tile).toBe('t0_1_0');
    const derived = a.key(continued.lane, continued.n);
    expect(cars(a, later).has(derived)).toBe(true);
    for (const t of [T0, T0 + 5, later]) {
      const ca = cars(a, t, claimsA), cb = cars(b, t, claimsB);
      expect(ca.has(key) || ca.has(derived)).toBe(false);
      expect([...ca.keys()].sort()).toEqual([...cb.keys()].sort());
    }
  });
});

describe('roads.json revision 2 (G3)', () => {
  // docs/specs/roads-json-v2.md on local/g3-roads: an east-west 2+1 street from the west seam to a signalised
  // junction, a north-south 1+1 street through it, a service road and a bus-only street that carry no traffic.
  const columns = ['way', 's0', 's1', 'length', 'lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'foot_density', 'flags', 'highway', 'setback0', 'setback1'];
  const json = {
    format: 'turku3d-roadgraph', version: 1, revision: 2, frame: 'engine-xz', bbox: [0, 0, 250, 250] as [number, number, number, number],
    nodes: [0, 100, 120, 100, 120, 20, 120, 200, 200, 100, 120, 240],
    edges: [[0, 1, STREET, 120], [2, 1, STREET, 80], [1, 3, STREET, 100], [1, 4, CAR | 1 << 3, 80], [3, 5, STREET, 40]],
    edgeHeights: [[3, 4.2], [5, 4.2], [4.2, 4.0], [4.2, 4.1], [4.0, 4.4]],
    edgeIds: ['00000000000000aa', '00000000000000bb', '00000000000000bb', '00000000000000cc', '00000000000000dd'],
    edgeAttrColumns: columns,
    edgeAttrs: [[11, 30, 150, 150, 2, 1, 9.6, 40, 8, 6, 0, 4, 0, 6.5], [12, 0, 80, 180, 1, 1, 6.5, 30, 4, 5, 0, 6, 0, 0],
      [12, 80, 180, 180, 1, 1, 6.5, 30, 4, 5, 0, 6, 0, 0], [13, 0, 80, 80, 1, 1, 5, 20, 2, 3, 0, 9, 4, 0],
      [14, 0, 40, 40, 1, 1, 6, 30, 4, 5, 4096, 6, 0, 0]],
    edgeEvents: [[118, 1 | 16, 0], null, null, null, null],
    nodeFlags: [32, 1 | 2, 0, 0, 0, 0],
    controllers: [['4be1f0a29c3d7e65', 120, 100]],
  };
  test('lanes, widths, heights, seams, setbacks and densities come from the data', () => {
    const roads = trafficRoads(json as unknown as RoadGraphJson);
    expect(roads.revision).toBe(2);
    // The service road (density 2) and the bus-only street carry nothing.
    expect(roads.edges.map(e => [e.forward, e.backward, e.width])).toEqual([[2, 1, 9.6], [1, 1, 6.5], [1, 1, 6.5]]);
    expect(roads.seams).toEqual(new Set([0]));
    expect(roads.signals).toEqual(new Map([[1, '4be1f0a29c3d7e65']]));
    // Stable keys follow edgeIds and spans, never the row order.
    expect(new Set(roads.edges.map(e => e.key)).size).toBe(3);
    const tile = buildTileTraffic('t0_0_0', roads, [0, 0, 250, 250], 'h');
    const eastWest = tile.lanes.filter(l => Math.abs(l.points[1] - l.points[l.points.length - 1]) < 1);
    // Divider at -1.6 m (south is right of eastbound): eastbound lanes on z 100 and 103.2, westbound on 96.8.
    const zs = (east: boolean) => eastWest.filter(l => (l.points[2] > l.points[0]) === east).map(l => Math.round(l.points[1] * 10) / 10).sort();
    expect([zs(true), zs(false)]).toEqual([[100, 103.2], [96.8]]);
    expect(eastWest.every(l => l.ys !== null)).toBe(true);
    // The eastbound lanes start on the seam and stop 6.5 m (the setback) before the junction node.
    const east = eastWest.filter(l => l.points[2] > l.points[0]);
    expect(east.every(l => l.start.kind === 'seam' && Math.abs(l.points[l.points.length - 2] - 113.5) < 0.01)).toBe(true);
    expect(tile.lanes.filter(l => l.next).length).toBeGreaterThan(2);
  });
});

describe('generator-written revision 2 tiles (tests/fixtures/roads-v2-seam.json)', () => {
  const fixture = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as { a: RoadGraphJson; b: RoadGraphJson };
  const tiles = new Map([['t0_0_0', fixture.a], ['t0_1_0', fixture.b]]);
  test('lanes join across the seam, cars cross it, and load order does not matter', () => {
    const ab = network(tiles, ['t0_0_0', 't0_1_0']), ba = network(tiles, ['t0_1_0', 't0_0_0']);
    const lanes = [...ab.tiles.values()].flatMap(t => t.lanes);
    expect(lanes.length).toBeGreaterThan(4);
    expect(lanes.every(l => l.ys !== null)).toBe(true);
    const crossing = lanes.filter(l => l.seamNext);
    expect(crossing.length).toBeGreaterThanOrEqual(2);
    for (const lane of crossing) expect(lane.seamNext!.tile).not.toBe(lane.tile);
    let crossed = 0;
    for (let k = 0; k < 120; k++) {
      const t = T0 + k * 0.5, ca = cars(ab, t), cb = cars(ba, t);
      expect([...ca.keys()].sort()).toEqual([...cb.keys()].sort());
      for (const [key, pose] of ca) expect(cb.get(key)).toEqual(pose);
    }
    for (const lane of crossing) {
      const [n] = ab.slots(lane, T0);
      const after = ab.advance(lane, n, T0 + laneSpan(lane) + 1);
      if (after && after.lane.tile !== lane.tile) crossed++;
    }
    expect(crossed).toBeGreaterThan(0);
  });
});

describe('population ring', () => {
  test('in view a car appears only far away; out of view from 40 m; removal is asymmetric', () => {
    const view = { x: 0, z: 0, fx: 0, fz: -1, halfFov: 0.6 };
    expect(inView(view, 0, -50)).toBe(true);
    expect(inView(view, 0, 50)).toBe(false);
    expect(inView(view, 60, -10)).toBe(false);
    expect(mayCreate(60, true, false)).toBe(false);
    expect(mayCreate(RING.createInViewMin + 1, true, false)).toBe(true);
    expect(mayCreate(RING.createInViewMax + 1, true, false)).toBe(false);
    expect(mayCreate(30, false, false)).toBe(false);
    expect(mayCreate(50, false, false)).toBe(true);
    expect(mayCreate(RING.removeOutOfView + 1, false, false)).toBe(false);
    expect(mayCreate(10, true, true)).toBe(true);
    expect(mustRemove(RING.removeOutOfView + 1, false)).toBe(true);
    expect(mustRemove(RING.removeOutOfView + 1, true)).toBe(false);
    expect(mustRemove(RING.removeInView + 1, true)).toBe(true);
  });

  test('the worker fills the cap, then creates cars only outside the near view cone, and claims on contact', () => {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    const spawned: string[] = [];
    let driven: number | null = null;
    const host: TrafficHost = { ground: () => 0, resident: () => true, spawnVehicle: (_p, _y, spec) => spawned.push(spec.id),
      bodyKind: handle => handle === driven ? 'driven' : null, character: () => null, player: () => ({ x: 200, y: 0, z: 100 }),
      pushCharacter: () => {} };
    const sim = new TrafficSim(R, world, host);
    const tiles = city();
    sim.setTiles([...tiles].map(([tile, roads]) => ({ tile, hash: roadHash(roads), bounds: [...tileBounds(tile)!] as [number, number, number, number], roads })), []);
    // Every slot carries a car (the conveyor's capacity), so the ring alone decides what is drawn.
    sim.configure({ cap: 12, density: 1e6 });
    const view = { x: 200, z: 100, fx: 1, fz: 0, halfFov: 0.7 };
    sim.setView(view);
    let t = T0;
    for (let i = 0; i < 30; i++) sim.beforeStep(t += 1 / 60);
    // Heights are sampled lazily: by now every drawn car has a pose.
    const first = new Set(sim.activeKeys());
    expect(first.size).toBe(12);
    const flat = sim.publish();
    expect(flat.length).toBe(12 * TRAFFIC_STRIDE);
    // Drive time on, turning the camera: a new car appears in view only from createInViewMin out, out of view
    // only from createOutOfViewMin; drawn cars stay inside the removal radii; bodies only near the player.
    const seen = new Set(sim.activeCars().map(c => c.handle));
    let created = 0, inViewCreated = 0;
    for (let i = 0; i < 60 * 60; i++) {
      if (i % 600 === 0) { const a = i / 600; Object.assign(view, { fx: Math.cos(a), fz: Math.sin(a) }); }
      sim.beforeStep(t += 1 / 60);
      for (const car of sim.activeCars()) {
        const d = Math.hypot(car.x - view.x, car.z - view.z), visible = inView(view, car.x, car.z);
        // Removal runs at 5 Hz: right after the camera turns, cars that left the view wait for the next pass.
        if (i % 600 > 15) expect(d).toBeLessThan((visible ? RING.removeInView : RING.removeOutOfView) + 4);
        if (car.body) expect(Math.hypot(car.x - 200, car.z - 100)).toBeLessThan(RING.bodyOff + 1);
        if (seen.has(car.handle)) continue;
        seen.add(car.handle); created++;
        if (visible) { inViewCreated++; expect(d).toBeGreaterThan(RING.createInViewMin - 3); }
        else expect(d).toBeGreaterThan(RING.createOutOfViewMin - 3);
      }
      expect(sim.activeKeys().length).toBeLessThanOrEqual(12);
    }
    expect(created).toBeGreaterThan(10);
    // With everything in view and room under a raised cap, every new car appears far out.
    Object.assign(view, { halfFov: 3.1 });
    sim.configure({ cap: 24 });
    const before = new Set(sim.activeCars().map(c => c.handle));
    sim.beforeStep(t += 1 / 60);
    const added = sim.activeCars().filter(c => !before.has(c.handle));
    expect(added.length).toBeGreaterThan(3);
    // Smoothing near a junction shifts a car a few metres from its timetable position.
    for (const car of added) expect(Math.hypot(car.x - view.x, car.z - view.z)).toBeGreaterThan(RING.createInViewMin - 8);
    void inViewCreated;
    // A car the driven vehicle touches turns into a dynamic vehicle with a registry-safe id; the event repeats
    // until the snapshot that carried it is acknowledged.
    const handle = sim.publish()[0];
    const id = sim.claim(handle)!;
    expect(id).toMatch(/^tr:t0_[01]_0:[0-9a-f]{8}:-?\d+$/);
    expect(`park:${id}`).toMatch(/^[A-Za-z0-9_.:-]{1,180}$/);
    expect(spawned).toEqual([id]);
    expect(sim.activeKeys()).not.toContain(id.slice(3));
    expect(sim.pending(0, 7).map(e => [e.id, e.revision])).toEqual([[id, 7]]);
    expect(sim.pending(6, 8)).toHaveLength(1);
    expect(sim.pending(7, 9)).toHaveLength(0);
    void driven;
  });
});

describe('rendering', () => {
  test.each([false, true])('0 to 40 moving cars share the parked batches: no new draw, program or pipeline (velocity=%s)', async motion => {
    const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
    renderer.hasFeature = () => false;
    Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
    if (motion) {
      const { mrt, normalView, velocity } = await import('three/tsl');
      renderer.setRenderTarget(new THREE.RenderTarget()); renderer.setMRT(mrt({ output: normalView, velocity }));
    }
    const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string } };
    const program = (object: THREE.Mesh) => {
      const builder = backend.createNodeBuilder(object, renderer);
      renderer.debug.onNodeBuilderCreated!(builder, { object } as never);
      builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera(); builder.build();
      return { vertexShader: builder.vertexShader, fragmentShader: builder.fragmentShader };
    };
    const geometries = new Map<string, THREE.BufferGeometry>();
    const load = vi.spyOn(GLTFLoader.prototype, 'loadAsync').mockImplementation(async url => {
      const model = new URL(url).pathname.slice(1).split('~')[0];
      if (!geometries.has(model)) geometries.set(model, new THREE.BoxGeometry(1.8, 1.5, 4.5));
      const scene = new THREE.Group();
      scene.add(new THREE.Mesh(geometries.get(model), new THREE.MeshStandardMaterial({ map: new THREE.DataTexture(new Uint8Array(4), 1, 1) })));
      return { scene } as GLTF;
    });
    try {
      const manifest = draftManifest('http://localhost/');
      manifest.vehicle_models = Object.fromEntries(TRAFFIC_MODELS.map(m => [`${m}/red`, { url: `${m}~red.glb`, sha256: '', bytes: 1 }]));
      manifest.vehicles = [{ object_id: 'p1', model: 'sedan', color: 'red', x: 0, y: 0, z: 0, yaw: 0, width: 1.85, length: 4.55, height: 1.48, clear_front_m: 9, clear_rear_m: 9 }];
      const scene = new THREE.Scene(), visuals = new ParkedCarVisuals(scene);
      visuals.reserveTraffic(40);
      await visuals.load(manifest);
      const meshes = [...scene.children] as THREE.InstancedMesh[];
      expect(meshes.map(m => m.name).sort()).toEqual(TRAFFIC_MODELS.map(m => `parked/${m}`).sort());
      const states: string[] = [];
      for (const count of [0, 1, 10, 40]) {
        const moving = Array.from({ length: count }, (_, i) => ({ model: TRAFFIC_MODELS[i % 4], color: ['blue', 'white', 'beige'][i % 3],
          x: i * 7, y: 0.75, z: 3, q: { x: 0, y: 0, z: 0, w: 1 } }));
        visuals.setTraffic(moving);
        visuals.update([], { restingPose: c => ({ pos: { x: c.x, y: c.y + 0.75, z: c.z }, rot: { x: 0, y: 0, z: 0, w: 1 } }) });
        expect(scene.children).toEqual(meshes);
        expect(meshes.reduce((n, m) => n + m.count, 0)).toBe(1 + count);
        const sedan = meshes.find(m => m.name === 'parked/sedan')!;
        if (count) {
          // The first moving sedan follows the parked one, with its own paint.
          const paint = Array.from(instancePaintOf(sedan)!.array.slice(3, 6));
          expect(paint).toEqual(paintOf('blue').map(Math.fround));
          const m = new THREE.Matrix4(); sedan.getMatrixAt(1, m);
          expect(new THREE.Vector3().setFromMatrixPosition(m).toArray()).toEqual([0, 0, 3]);
        }
        for (const mesh of meshes) { const s = program(mesh); states.push(s.vertexShader + s.fragmentShader); }
      }
      expect(new Set(states).size).toBe(1);
      const pipelineBackend = { createProgram: vi.fn(), createRenderPipeline: vi.fn(), needsRenderUpdate: () => false, getRenderCacheKey: () => 'pass' };
      const pipelines = new Pipelines(pipelineBackend, {}, { createProgram: vi.fn(), destroyProgram: vi.fn() });
      const all = meshes.map(program);
      for (const state of all) pipelines.getForRender({ material: { name: 'parked' }, getNodeBuilderState: () => state });
      expect(pipelineBackend.createRenderPipeline).toHaveBeenCalledTimes(1);
      pipelines.dispose();
    } finally { load.mockRestore(); }
  });
});

describe('traffic density', () => {
  // One tile of revision 2 (spec §3.5 highway names): a secondary street from the west seam to the east seam, a
  // residential street and a living street off it (both dead ends), a service road and a pedestrian street.
  const columns = ['way', 's0', 's1', 'length', 'lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'foot_density', 'flags', 'highway', 'setback0', 'setback1'];
  const HIGHWAYS = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street', 'service', 'pedestrian'];
  const SECONDARY = CAR | FOOT | 3 << 3, SERVICE = CAR | FOOT | 1 << 3, PATH = FOOT;
  const street = (way: number, length: number, kmh: number, car: number, highway: string, flags = 0) =>
    [way, 0, length, length, 1, 1, highway === 'secondary' ? 7 : 5.5, kmh, car, 5, flags, HIGHWAYS.indexOf(highway), 0, 0];
  const mixed = {
    format: 'turku3d-roadgraph', version: 1, revision: 2, frame: 'engine-xz', bbox: [0, 0, 250, 250],
    bits: { highways: HIGHWAYS },
    nodes: [0, 60, 80, 60, 170, 60, 250, 60, 80, 200, 170, 200, 40, 120, 80, 120],
    edges: [[0, 1, SECONDARY, 80], [1, 2, SECONDARY, 90], [2, 3, SECONDARY, 80], [1, 7, STREET, 60], [7, 4, STREET, 80],
      [7, 6, SERVICE, 40], [2, 5, STREET, 140], [4, 5, PATH, 90]],
    edgeHeights: [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]],
    edgeIds: ['00000000000000a0', '00000000000000a1', '00000000000000a2', '00000000000000b0', '00000000000000b1',
      '00000000000000c0', '00000000000000d0', '00000000000000e0'],
    edgeAttrColumns: columns,
    edgeAttrs: [street(1, 80, 40, 11, 'secondary'), street(1, 90, 40, 11, 'secondary'), street(1, 80, 40, 11, 'secondary'),
      street(2, 60, 30, 4, 'residential'), street(2, 80, 30, 4, 'residential'), street(3, 40, 20, 2, 'service'),
      street(4, 140, 20, 1, 'living_street', 2048), [5, 0, 90, 90, 0, 0, 6, 10, 0, 12, 2048, HIGHWAYS.indexOf('pedestrian'), 0, 0]],
    edgeEvents: [null, null, null, null, null, null, null, null],
    nodeFlags: [32, 1, 1, 32, 0, 0, 0, 1],
    controllers: [],
  } as unknown as RoadGraphJson;
  const pinned = (hour: number, multiplier = 1): TrafficDensity => ({ hourAt: () => hour, multiplier });
  /** The shared day cycle, as the worker follows it in multiplayer. */
  const shared: TrafficDensity = { hourAt: t => cycleHour(TRAFFIC.epochMs + t * 1000).hour, multiplier: 1 };
  const kind = (lane: Lane) => Math.abs(lane.points[1] - lane.points[lane.points.length - 1]) < 1 ? 'secondary'
    : lane.points[0] < 120 ? 'residential' : 'living';
  /** Cars per hour on each lane, by kind of street, over `hours` hours of slots. */
  const rates = (net: TrafficNetwork, hours: number) => {
    const out: Record<string, number[]> = {};
    for (const tile of net.tiles.values()) for (const lane of tile.lanes) {
      let count = 0;
      for (let n = 0; n < hours * 3600 / H; n++) if (net.present(lane, n)) count++;
      (out[kind(lane)] ??= []).push(count / hours);
    }
    return out;
  };
  const mean = (list: number[]) => list.reduce((a, b) => a + b, 0) / list.length;

  test('the daily curve is low at night, peaks in the morning and the afternoon, and has no steps', () => {
    expect(HOURLY).toHaveLength(24);
    for (let h = 0; h < 24; h++) expect(profile(h + 0.5)).toBeCloseTo(HOURLY[h], 9);
    for (const h of [1, 2, 3, 4]) expect(profile(h)).toBeLessThan(0.05);
    expect(profile(15.5)).toBe(1);
    // Two peaks: the morning one (7–8) and the afternoon one (15–16) stand above the hours around them.
    expect(profile(7.5)).toBeGreaterThan(Math.max(profile(5.5), profile(10.5)));
    expect(profile(15.5)).toBeGreaterThan(Math.max(profile(12.5), profile(19.5)));
    expect(profile(22)).toBeLessThan(0.2);
    expect(profile(13)).toBeGreaterThan(3 * profile(22));
    let step = 0;
    for (let m = 0; m < 24 * 60; m++) step = Math.max(step, Math.abs(profile((m + 1) / 60) - profile(m / 60)));
    expect(step).toBeLessThan(0.01);
    expect(profile(23.999)).toBeCloseTo(profile(-0.001), 3);
  });

  test('living streets carry a rare car, pedestrian streets and service roads none, main streets follow the clock', () => {
    const roads = trafficRoads(mixed);
    // The service road and the pedestrian street have no car lanes; the living street has, at an access rate.
    expect(roads.edges.map(e => Math.round(e.flow))).toEqual([420, 420, 420, 59, 59, 8]);
    const net = new TrafficNetwork();
    net.setTile(buildTileTraffic('t0_0_0', roads, [0, 0, 250, 250], roadHash(mixed)));
    expect(new Set(net.tiles.get('t0_0_0')!.lanes.map(kind))).toEqual(new Set(['secondary', 'residential', 'living']));
    for (const [hour, livingMax] of [[3, 0.5], [8, 7], [13, 7], [16, 10], [22, 2]] as const) {
      net.density = pinned(hour);
      const r = rates(net, 40);
      // Within sampling noise of the model: peak flow × profile per lane and hour.
      expect(mean(r.secondary)).toBeGreaterThan(0.8 * 420 * profile(hour) - 3);
      expect(mean(r.secondary)).toBeLessThan(1.2 * 420 * profile(hour) + 3);
      for (const living of r.living) expect(living).toBeLessThanOrEqual(livingMax);
      if (hour === 13 || hour === 16) expect(mean(r.secondary)).toBeGreaterThan(25 * Math.max(0.5, mean(r.living)));
    }
    // Before, every slot carried a car: 600 an hour on every lane, the living street's too.
    net.density = null;
    expect(rates(net, 1).living.every(v => v === 600)).toBe(true);
  });

  test('two clients thin alike on the shared cycle, whatever their load order; a lower multiplier is a subset', () => {
    const tiles = city();
    tiles.set('t0_0_1', { ...mixed, bbox: [...tileBounds('t0_0_1')!] as [number, number, number, number],
      nodes: mixed.nodes.map((v, i) => i % 2 ? v + 250 : v) });
    const a = network(tiles, ['t0_0_0', 't0_1_0', 't0_0_1']), b = network(tiles, ['t0_0_1', 't0_1_0', 't0_0_0']);
    a.density = shared; b.density = { ...shared };
    let total = 0;
    // Samples over one whole 30-minute day cycle.
    for (let k = 0; k < 300; k++) {
      const t = T0 + k * 6.1, ca = cars(a, t), cb = cars(b, t);
      expect([...ca.keys()].sort()).toEqual([...cb.keys()].sort());
      for (const [key, pose] of ca) expect(cb.get(key)).toEqual(pose);
      total += ca.size;
    }
    expect(total).toBeGreaterThan(300);
    const half = network(tiles);
    half.density = { ...shared, multiplier: 0.5 };
    for (let k = 0; k < 60; k++) {
      const t = T0 + k * 29.3, full = cars(a, t), fewer = cars(half, t);
      for (const key of fewer.keys()) expect(full.has(key)).toBe(true);
    }
  });

  test('a car keeps its presence across a seam where both chains anchor at that crossing', () => {
    const net = network(city());
    net.density = pinned(13);
    let checked = 0;
    for (const tile of net.tiles.values()) for (const lane of tile.lanes) {
      const next = lane.seamNext;
      if (!next || lane.anchor.key !== next.anchor.key) continue;
      const fa = slotShare(lane.flow, 13), fb = slotShare(next.flow, 13);
      for (let n = -400; n < 400; n++) {
        const c = net.continuation(lane, n)!;
        const before = net.present(lane, n), after = net.present(c.lane, c.n);
        // The same hash against each lane's share: presence only changes where the shares differ.
        if (before && fb >= fa) expect(after).toBe(true);
        if (after && fa >= fb) expect(before).toBe(true);
        checked++;
      }
    }
    expect(checked).toBeGreaterThanOrEqual(800);
  });

  test('the drawn set thins without pops: a car whose slot goes empty stays until it is out of view', () => {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    const host: TrafficHost = { ground: () => 0, resident: () => true, spawnVehicle: () => {}, bodyKind: () => null,
      character: () => null, player: () => ({ x: 200, y: 0, z: 100 }), pushCharacter: () => {} };
    const sim = new TrafficSim(R, world, host);
    sim.setTiles([...city()].map(([tile, roads]) => ({ tile, hash: roadHash(roads), bounds: [...tileBounds(tile)!] as [number, number, number, number], roads })), []);
    sim.configure({ cap: 40, clock: { kind: 'hour', hour: 16 } });
    const view = { x: 200, z: 100, fx: 1, fz: 0, halfFov: 0.7 };
    sim.setView(view);
    let t = T0, deferred = 0, nearPops = 0;
    const last = new Map<number, { x: number; z: number; visible: boolean }>();
    const drawn: Record<number, number[]> = { 16: [], 22: [], 3: [] };
    for (let i = 0; i < 120 * 60; i++) {
      // The clock falls from the afternoon peak to late evening, then to night; the camera turns every 15 s.
      if (i === 30 * 60) sim.configure({ clock: { kind: 'hour', hour: 22 } });
      if (i === 75 * 60) sim.configure({ clock: { kind: 'hour', hour: 3 } });
      if (i % 900 === 0) Object.assign(view, { fx: Math.cos(i / 900 * 1.3), fz: Math.sin(i / 900 * 1.3) });
      sim.beforeStep(t += 1 / 60);
      const now = new Map(sim.activeCars().map(c => [c.handle, c]));
      for (const [handle, before] of last) {
        if (!now.has(handle) && before.visible && Math.hypot(before.x - view.x, before.z - view.z) < RING.createInViewMin) nearPops++;
      }
      last.clear();
      for (const c of now.values()) last.set(c.handle, { x: c.x, z: c.z, visible: inView(view, c.x, c.z) });
      deferred = Math.max(deferred, sim.stats.deferred);
      drawn[i < 30 * 60 ? 16 : i < 75 * 60 ? 22 : 3].push(now.size);
    }
    // No car vanished in the near view, although cars were left on emptied slots (they went once out of view).
    expect(nearPops).toBe(0);
    expect(deferred).toBeGreaterThan(0);
    const settled = (hour: number) => mean(drawn[hour].slice(-10 * 60));
    expect(settled(16)).toBeGreaterThan(2 * settled(22));
    expect(settled(22)).toBeGreaterThan(settled(3));
    expect(sim.stats.hour).toBe(3);
  });
});
