import { describe, expect, test } from 'vitest';
import { CAR, EMPTY_ROAD_GRAPH, FOOT, ONEWAY, loadRoadGraphJson, mergeRoadGraphs, parseRoadGraph, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { MAX_SNAP_M, StreetRouter } from '../../src/gameplay/router';
import type { WorldPoint } from '../../src/gameplay/contract';
import { streetGrid } from '../fixtures/streetGrid';

const STREET = FOOT | CAR | 2 << 3;

// 100 m square: three street sides (one bent through (100,50)) and a footway on the west side.
const square = (eastFlags = STREET) => parseRoadGraph({
  format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, 100, 100],
  nodes: [0, 0, 100, 0, 100, 100, 0, 100],
  edges: [[0, 1, STREET, 100], eastFlags & ONEWAY ? [2, 1, eastFlags, 100, 100, 50] : [1, 2, eastFlags, 100, 100, 50],
    [2, 3, STREET, 100], [3, 0, FOOT, 100]],
});
const router = (mode: 'foot' | 'car' = 'foot', graph = square()) => Object.assign(new StreetRouter(graph), { mode });
const rounded = (path: WorldPoint[] | null) => path?.map(p => [Math.round(p.x * 10) / 10, Math.round(p.z * 10) / 10]);

describe('road graph router', () => {
  test('snaps by edge projection, not to the nearest node', () => {
    const snap = router().snap({ x: 40, z: 6 })!;
    expect(snap.point).toEqual({ x: 40, z: 0 });
    expect(snap.offset).toBeCloseTo(40);
    expect(snap.distance).toBeCloseTo(6);
    expect(router().snap({ x: 50, z: -MAX_SNAP_M - 1 })).toBeNull();
  });

  test('walks the footway, drives around it, and includes both snapped endpoints and bends', () => {
    const from = { x: 50, z: -3 }, to = { x: 20, z: 97 };
    const walk = router('foot').route(from, to);
    expect(rounded(walk)).toEqual([[50, 0], [0, 0], [0, 100], [20, 100]]);
    expect(router().distance(walk!)).toBeCloseTo(170);
    const drive = router('car').route(from, to);
    expect(rounded(drive)).toEqual([[50, 0], [100, 0], [100, 50], [100, 100], [20, 100]]);
    expect(router().distance(drive!)).toBeCloseTo(230);
  });

  test('car routes respect oneway edges and walkers ignore them', () => {
    const graph = square(STREET | ONEWAY);
    const from = { x: 50, z: 3 }, to = { x: 20, z: 97 };
    expect(router('car', graph).route(from, to)).toBeNull();
    expect(rounded(router('car', graph).route(to, from))).toEqual([[20, 100], [100, 100], [100, 50], [100, 0], [50, 0]]);
    expect(rounded(router('foot', graph).route(from, to))).toEqual([[50, 0], [0, 0], [0, 100], [20, 100]]);
  });

  test('both ends on one edge go straight unless a oneway forbids that direction', () => {
    expect(rounded(router('car').route({ x: 10, z: 1 }, { x: 60, z: 1 }))).toEqual([[10, 0], [60, 0]]);
    const graph = square(STREET | ONEWAY);
    const back = router('car', graph).route({ x: 101, z: 30 }, { x: 101, z: 80 });
    expect(back).toBeNull(); // southbound on a northbound-only street with no car loop
    expect(rounded(router('car', graph).route({ x: 101, z: 80 }, { x: 101, z: 30 }))).toEqual([[100, 80], [100, 50], [100, 30]]);
  });

  test('off-graph ends and the empty graph give null', () => {
    expect(router().route({ x: 50, z: 0 }, { x: 900, z: 900 })).toBeNull();
    expect(new StreetRouter(EMPTY_ROAD_GRAPH).route({ x: 0, z: 0 }, { x: 1, z: 1 })).toBeNull();
    expect(router().distance([])).toBe(0);
  });

  test('rejects other formats and broken edges; a manifest without roads has no graph', async () => {
    expect(() => parseRoadGraph({ format: 'x', version: 1, frame: 'engine-xz', bbox: [0, 0, 0, 0], nodes: [], edges: [] })).toThrow();
    expect(() => parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, 0, 0], nodes: [0, 0], edges: [[0, 3, FOOT, 1]] })).toThrow();
    expect(await loadRoadGraphJson({ baseUrl: 'http://localhost/' })).toBeNull();
    expect(mergeRoadGraphs([])).toBe(EMPTY_ROAD_GRAPH);
  });

  // Its time budget is an unloaded-host check: tests/perf/router.test.ts (make streaming-perf).
  test('A* crosses a 60 x 60 street grid on a shortest path', () => {
    const grid = streetGrid(60);
    const path = grid.route({ x: 3, z: 1 }, { x: 1170, z: 1177 })!;
    expect(grid.distance(path)).toBeCloseTo(1180 - 3 + 1180 - 10, 0);
  });
});

describe('draft tiles join the map', () => {
  const part = (bbox: [number, number, number, number], nodes: number[], edges: number[][]): RoadGraphJson =>
    ({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox, nodes, edges });
  // The published core (x 0..500) and a draft east of it (x 500..750): Main Street crosses the shared edge at
  // (500, 100), each part ending on it after its own clip (the draft's copy carries float noise below 0.1 m).
  const core = part([0, 0, 500, 500], [400, 100, 500, 100, 400, 300], [[0, 1, STREET, 100], [0, 2, STREET, 200]]);
  const draft = part([500, 0, 750, 250], [500.03, 99.98, 700, 100, 700, 200], [[0, 1, STREET, 200], [1, 2, FOOT, 100]]);

  test('parts merge at their shared edge points and an edge both carry is kept once', () => {
    const merged = mergeRoadGraphs([core, draft, core]);
    expect(merged.nodes.length / 2).toBe(5);
    expect(merged.edges).toHaveLength(4);
    expect(merged.bbox).toEqual([0, 0, 750, 500]);
  });

  test('the router routes from the published streets onto a draft tile once its graph arrives', () => {
    const r = new StreetRouter(mergeRoadGraphs([core]));
    expect(r.route({ x: 400, z: 300 }, { x: 700, z: 200 })).toBeNull(); // off the graph
    r.setGraph(mergeRoadGraphs([core, draft]));
    const path = r.route({ x: 400, z: 300 }, { x: 700, z: 200 })!;
    expect(rounded(path)).toEqual([[400, 300], [400, 100], [500, 100], [700, 100], [700, 200]]);
    expect(r.distance(path)).toBeCloseTo(600, 0);
  });
});
