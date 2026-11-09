import { describe, expect, test } from 'vitest';
import { tileBounds } from '../../src/render/lights/tileLights';
import { TRAFFIC } from '../../src/traffic/constants';
import { TrafficFlow, packCar, unpackCar, type TrafficSnapshot } from '../../src/traffic/flow';
import { TrafficNetwork } from '../../src/traffic/network';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic } from '../../src/traffic/tileNetwork';
import { city } from './traffic-city.fixture';

// Multiplayer traffic authority (server/roads.js): the owner of a shared tile sends its cars there; a client
// holding another set of tiles takes them over (TrafficFlow.mirror) instead of running its own sources there.
const graphs = city();

function flow(tiles: string[]) {
  const net = new TrafficNetwork();
  net.density = { hourAt: () => 16, multiplier: 1 };
  for (const id of tiles) net.setTile(buildTileTraffic(id, trafficRoads(structuredClone(graphs.get(id)!)), tileBounds(id)!, id));
  const f = new TrafficFlow(net, 0);
  f.reset(0);
  return f;
}

const onTile = (f: TrafficFlow, tile: string) => new Map([...f.cars.values()].filter(c => c.lane.tile === tile).map(c => [c.key, c]));

/** Runs an owner holding both tiles and a client holding only t0_1_0 on one clock for `seconds`. With `mirror`, the
 *  client takes the owner's t0_1_0 snapshot every TRAFFIC.mirrorTicks, one tick after it was taken (the relay).
 *  Returns, per tick after the first snapshot, how many of the owner's t0_1_0 cars the client had at the same place. */
function run(seconds: number, mirror: boolean) {
  const owner = flow(['t0_0_0', 't0_1_0']), client = flow(['t0_1_0']);
  if (mirror) client.setRemote(['t0_1_0']);
  let pending: TrafficSnapshot | null = null, matched = 0, total = 0, foreign = 0, mirrored = 0;
  const seen = new Set<string>();
  for (let t = 0.1; t <= seconds; t += 0.1) {
    owner.update(t); client.update(t);
    if (pending) { mirrored += client.mirror(JSON.parse(JSON.stringify(pending)) as TrafficSnapshot); pending = null; }
    for (const key of owner.cars.keys()) seen.add(key);
    if (mirror && owner.tick % TRAFFIC.mirrorTicks === 0) pending = owner.snapshot(['t0_1_0']);
    if (t < 20) continue;
    const mine = onTile(owner, 't0_1_0'), theirs = onTile(client, 't0_1_0');
    for (const [key, car] of mine) {
      total++;
      const other = theirs.get(key);
      if (other && other.lane.tile === car.lane.tile && other.lane.id === car.lane.id && Math.abs(other.s - car.s) < 1.5) matched++;
    }
    for (const key of theirs.keys()) if (!seen.has(key)) foreign++;
  }
  return { share: matched / Math.max(1, total), total, foreign, mirrored };
}

describe('traffic authority mirror', () => {
  test('a client mirroring the owner draws the owner\'s cars at the owner\'s places, and none of its own', () => {
    // Without the mirror, the client admits its own cars where the owner's come over the seam.
    const alone = run(120, false);
    expect(alone.total).toBeGreaterThan(200);
    expect(alone.foreign).toBeGreaterThan(50);
    const { share, total, foreign, mirrored } = run(120, true);
    expect(total).toBeGreaterThan(200);
    expect(mirrored).toBeGreaterThan(50);
    expect(share).toBeGreaterThan(Math.max(0.95, alone.share));
    // Every car the client drew there is one the owner ran: no source of a remote tile admitted a car.
    expect(foreign).toBe(0);
  });

  test('a mirror car record carries every non-default state and nothing a receiver cannot check', () => {
    const owner = flow(['t0_0_0', 't0_1_0']);
    for (let t = 0.1; t <= 60; t += 0.1) owner.update(t);
    const snapshot = owner.snapshot(['t0_0_0', 't0_1_0']);
    const cars = Object.values(snapshot.tiles).flat();
    expect(cars.length).toBeGreaterThan(5);
    // Compact: about 100 bytes a car (hashed lane ids), so a 3x3 block of busy tiles fits one room message.
    expect(JSON.stringify(snapshot).length / cars.length).toBeLessThan(110);
    const saved = owner.checkpoint().cars;
    for (const [tile, list] of Object.entries(snapshot.tiles)) for (const packed of list) {
      const back = unpackCar(JSON.parse(JSON.stringify(packed)), tile, snapshot.tick)!;
      const original = saved.find(c => c.key === back.key)!;
      expect(back.lane).toBe(original.lane);
      expect(back.route).toBe(original.route);
      expect(back.s).toBeCloseTo(original.s, 1);
      expect(back.change?.from ?? null).toBe(original.change?.from ?? null);
      expect(packCar(back, tile, snapshot.tick)).toEqual(packed);
    }
    const base = ['t0_0_0:1:4', '~1', '=', 0, 0, 0, 0, 0, 0];
    expect(unpackCar(base, 't0_0_0', 0)).toMatchObject({ key: 't0_0_0:1:4', lane: 't0_0_0:1', route: 't0_0_0:1', n: 4 });
    for (const bad of [null, [], ['k'], [...base, { evil: 1 }], ['t0_0_0:1:4', 'elsewhere:1', ...base.slice(2)], ['k:x', ...base.slice(1)],
      [...base.slice(0, 3), NaN, ...base.slice(4)], [...base, { c: { from: 5 } }], [...base, JSON.parse('{"__proto__": {"st": "x"}}')]]) {
      expect(unpackCar(bad, 't0_0_0', 0)).toBeNull();
    }
  });
});

describe('tile prefill', () => {
  test('a tile that joins a running flow starts with about the cars it has when present from the start', () => {
    // Reference: both tiles from the start, 3 minutes in. Late: only t0_0_0, t0_1_0 added at 3 minutes.
    const both = flow(['t0_0_0', 't0_1_0']), late = flow(['t0_0_0']);
    for (let t = 0.1; t <= 180; t += 0.1) { both.update(t); late.update(t); }
    const net = (late as unknown as { network: TrafficNetwork }).network;
    net.setTile(buildTileTraffic('t0_1_0', trafficRoads(structuredClone(graphs.get('t0_1_0')!)), tileBounds('t0_1_0')!, 't0_1_0'));
    late.update(180.1);
    const mature = onTile(both, 't0_1_0').size, joined = onTile(late, 't0_1_0').size;
    expect(mature).toBeGreaterThan(4);
    expect(late.stats.prefilled).toBeGreaterThan(0);
    expect(joined).toBeGreaterThan(mature * 0.5);
    // The prefilled cars are the reference's own drivers (the timetable's keys), not new identities.
    const shared = [...onTile(late, 't0_1_0').keys()].filter(key => onTile(both, 't0_1_0').has(key)).length;
    expect(shared).toBeGreaterThan(joined * 0.3);
    // And they drive on: a minute later the tile is as busy as the reference's, and no car sits stuck on it.
    for (let t = 180.2; t <= 240; t += 0.1) { both.update(t); late.update(t); }
    expect(onTile(late, 't0_1_0').size).toBeGreaterThan(onTile(both, 't0_1_0').size * 0.6);
  });
});
