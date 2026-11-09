import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import { mergeRoadGraphs, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic, type Lane } from '../../src/traffic/tileNetwork';
import { TrafficNetwork, entryTime } from '../../src/traffic/network';
import { LaneRouter } from '../../src/gameplay/autopilot/lanes';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as { a: RoadGraphJson; b: RoadGraphJson };
function shifted(json: RoadGraphJson, dz: number) {
  const out = structuredClone(json);
  out.seamOrigins = {};
  out.nodeFlags!.forEach((f, n) => {
    if (!(f & NODE.SEAM)) return;
    out.seamOrigins![n] = [out.nodes[2 * n], out.nodes[2 * n + 1]];
    out.nodes[2 * n + 1] += dz;
    for (const e of out.edges) if (e[0] === n || e[1] === n) {
      for (let i = 5; i < e.length; i += 2) e[i] += dz;
    }
  });
  return out;
}
function network(a: RoadGraphJson, b: RoadGraphJson, reverse = false) {
  const n = new TrafficNetwork();
  const tiles = [buildTileTraffic('t0_0_0', trafficRoads(a), a.bbox, 'a'), buildTileTraffic('t0_1_0', trafficRoads(b), b.bbox, 'b')];
  for (const t of reverse ? tiles.reverse() : tiles) n.setTile(t);
  return n;
}
const lanes = (n: TrafficNetwork) => [...n.tiles.values()].flatMap(t => t.lanes).sort((a, b) => (a.tile + a.id).localeCompare(b.tile + b.id));
const geometry = (n: TrafficNetwork) => lanes(n).map(l => [l.id, [...l.points], l.seamNext?.id]);
const heading = (l: Lane, start: boolean) => {
  const i = start ? 0 : l.points.length - 4;
  return Math.atan2(l.points[i + 3] - l.points[i + 1], l.points[i + 2] - l.points[i]);
};

describe('centred seam with a pinned older neighbour', () => {
  test('graph and minimap rejoin by original identity, in either load order', () => {
    const a = shifted(fixture.a, -2.8), b = fixture.b;
    const ab = mergeRoadGraphs([a, b]), ba = mergeRoadGraphs([b, a]);
    expect(ab).toEqual(ba);
    expect(ab.edges.filter(e => e.way === 11 && e.span[2] === 100)).toHaveLength(1);
    const rural = ab.edges.filter(e => e.way === 14);
    expect(rural).toHaveLength(2);
    expect(rural[0].b === rural[1].a || rural[1].b === rural[0].a).toBe(true);
    for (const e of ab.edges) {
      expect([...e.points.slice(0, 2)]).toEqual([...ab.nodes.slice(e.a * 2, e.a * 2 + 2)]);
      expect([...e.points.slice(-2)]).toEqual([...ab.nodes.slice(e.b * 2, e.b * 2 + 2)]);
      expect(e.heights?.length).toBe(e.points.length / 2);
    }
  });

  test('traffic crosses a 2.8 m generation difference without a gap, kink, phase jump or load-order dependence', () => {
    const a = shifted(fixture.a, -2.8), b = fixture.b, net = network(a, b);
    expect(geometry(network(a, b, true))).toEqual(geometry(net));
    const crossing = lanes(net).filter(l => l.seamNext);
    expect(crossing.length).toBeGreaterThan(2);
    for (const l of crossing) {
      const next = l.seamNext!;
      expect([...l.points.slice(-2)]).toEqual([...next.points.slice(0, 2)]);
      expect(Math.abs(heading(l, false) - heading(next, true))).toBeLessThan(0.04);
      const t = entryTime(l, 10) + l.tau;
      const after = net.continuation(l, 10)!;
      expect(Math.abs(entryTime(after.lane, after.n) - t)).toBeLessThan(0.025);
      const p = net.pose(l, 10, t - 0.001, () => 0), q = net.pose(after.lane, after.n, t + 0.001, () => 0);
      expect(Math.hypot(p.x - q.x, p.z - q.z)).toBeLessThan(0.05);
    }
    const before = geometry(net);
    net.removeTile('t0_1_0');
    net.setTile(buildTileTraffic('t0_1_0', trafficRoads(b), b.bbox, 'b'));
    expect(geometry(net)).toEqual(before);
  });

  test('autopilot can cross the mixed seam in both directions', () => {
    const router = new LaneRouter();
    router.setTile({ tile: 't0_0_0', hash: 'new', json: shifted(fixture.a, -2.8) });
    router.setTile({ tile: 't0_1_0', hash: 'old', json: fixture.b });
    const crossing = lanes(router.network).filter(l => l.seamNext);
    expect(crossing.some(l => l.tile === 't0_0_0')).toBe(true);
    expect(crossing.some(l => l.tile === 't0_1_0')).toBe(true);
    for (const [from, to] of [[{ x: 210, z: 99 }, { x: 290, z: 102 }], [{ x: 290, z: 98 }, { x: 210, z: 96 }]]) {
      const route = router.route(from, to);
      expect(route).not.toBeNull();
      expect(new Set(route!.lanes.map(l => l.tile)).size).toBe(2);
    }
  });

  test('a nearby unrelated seam does not match a moved endpoint', () => {
    const a = shifted(fixture.a, -2.8), b = structuredClone(fixture.b);
    b.nodeFlags!.forEach((f, n) => { if (f & NODE.SEAM) b.nodes[2 * n + 1] -= 2.8; });
    expect(lanes(network(a, b)).filter(l => l.seamNext)).toHaveLength(0);
  });
});
