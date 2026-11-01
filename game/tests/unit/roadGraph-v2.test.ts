import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import { CAR, EDGE, EVENT, FOOT, NODE, laneOffset, lanePolyline, mergeRoadGraphs, parseRoadGraph, speedTier,
  type RoadGraph, type RoadGraphJson } from '../../src/gameplay/roadGraph';

// Two neighbouring tiles (t0_0_0 | t0_1_0, seam x = 250) written by the generator from a synthetic town
// (pipeline/tests/test_roads_v2.py keeps the file in step with pipeline/turku3d/roads/tile_graph.py).
const fixture = JSON.parse(readFileSync(new URL('../fixtures/roads-v2-seam.json', import.meta.url), 'utf8')) as { a: RoadGraphJson; b: RoadGraphJson };
const { a, b } = fixture;

const snapshot = (graph: RoadGraph) => ({
  nodes: [...graph.nodes], nodeFlags: [...graph.nodeFlags], controllers: graph.controllers,
  edges: graph.edges.map(e => ({ a: e.a, b: e.b, flags: e.flags, id: e.id, span: e.span, lanes: e.lanes, kmh: e.kmh,
    points: [...e.points], heights: e.heights ? [...e.heights] : null, events: e.events, setbacks: e.setbacks })),
  adjacency: graph.adjacency,
});
const main = (graph: RoadGraph) => graph.edges.filter(e => e.way === 11 && e.span[2] === 100);

describe('roads.json v2', () => {
  test('any load order gives identical ids, indices, lanes and heights', () => {
    const ab = snapshot(mergeRoadGraphs([a, b])), ba = snapshot(mergeRoadGraphs([b, a]));
    expect(ba).toEqual(ab);
    expect(snapshot(mergeRoadGraphs([b, a, b, a]))).toEqual(ab);
  });

  test('pieces of one logical edge re-join across the seam', () => {
    const [left] = main(parseRoadGraph(a)), [right] = main(parseRoadGraph(b));
    expect(left.id).toBe(right.id);
    expect(left.span).toEqual([0, 50, 100]);
    expect(right.span).toEqual([50, 100, 100]);
    const graph = mergeRoadGraphs([a, b]);
    const joined = main(graph);
    expect(joined).toHaveLength(1);
    const [edge] = joined;
    expect(edge.id).toBe(left.id);
    expect(edge.span).toEqual([0, 100, 100]);
    expect(edge.cumulative[edge.cumulative.length - 1]).toBeCloseTo(100, 6);
    expect(edge.heights).toHaveLength(edge.points.length / 2);
    expect(edge.heights![edge.heights!.length - 1]).toBeCloseTo(0.01 * 300 + 2, 3);
    // Events keep their logical positions: zebra at 230, unmarked crossing at 270, signal stop at 290.
    const at = (kind: number) => edge.events.filter(e => (e.code & 15) === kind).map(e => Math.round(e.s * 10) / 10);
    expect(at(EVENT.CROSSING)).toEqual([30, 70]);
    expect(at(EVENT.SIGNAL)).toEqual([90]);
    const signal = edge.events.find(e => (e.code & 15) === EVENT.SIGNAL)!;
    expect(signal.code & EVENT.FORWARD).toBeTruthy();
    expect(graph.controllers[signal.controller].id).toBe(b.controllers![0][0]);
    // The seam point no longer ends an edge; the junctions keep their flags.
    expect(graph.nodes.length / 2).toBeLessThan(a.nodes.length / 2 + b.nodes.length / 2);
    const flagAt = (x: number, z: number) => {
      for (let i = 0; i < graph.nodes.length / 2; i++) if (graph.nodes[2 * i] === x && graph.nodes[2 * i + 1] === z) return graph.nodeFlags[i];
      return null;
    };
    expect(flagAt(250, 100)).toBeNull();
    expect(flagAt(200, 100)! & NODE.JUNCTION).toBeTruthy();
    expect(edge.eflags & EDGE.START_JUNCTION && edge.eflags & EDGE.END_JUNCTION).toBeTruthy();
  });

  test('lane geometry is right-hand traffic around the divider', () => {
    const [edge] = main(mergeRoadGraphs([b, a]));
    expect(edge.lanes).toEqual({ fwd: 1, bwd: 1, width: 6.5 });
    expect(laneOffset(edge, 1)).toBeCloseTo(1.625);
    expect(laneOffset(edge, -1)).toBeCloseTo(-1.625);
    // Eastbound (a→b) traffic drives on the south (+z) side.
    const forward = lanePolyline(edge, 1).points, backward = lanePolyline(edge, -1).points;
    for (let i = 1; i < forward.length; i += 2) {
      expect(forward[i]).toBeCloseTo(101.625);
      expect(backward[i]).toBeCloseTo(98.375);
    }
    expect(speedTier(edge)).toBe(2);
    const oneway = mergeRoadGraphs([a, b]).edges.find(e => e.way === 13 && e.flags & CAR)!;
    expect(oneway.lanes.bwd).toBe(0);
    expect(laneOffset(oneway, 1)).toBeCloseTo(0);
  });

  test('a long edge without junctions in reach is cut at the seam, with one id per tile', () => {
    const graph = mergeRoadGraphs([a, b]);
    const rural = graph.edges.filter(e => e.way === 14);
    expect(rural).toHaveLength(2);
    expect(rural[0].id).not.toBe(rural[1].id);
    for (const e of rural) expect(e.span[0] === 0 && e.span[1] === e.span[2]).toBe(true);
  });

  test('v1 parts still load and get fallback ids and lanes', () => {
    const v1: RoadGraphJson = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [500, 0, 750, 250],
      nodes: [500, 10, 600, 10], edges: [[0, 1, FOOT | CAR | 2 << 3, 100]] };
    const graph = mergeRoadGraphs([a, v1]);
    const edge = graph.edges.find(e => e.id.startsWith('v1:'))!;
    expect(edge.lanes).toEqual({ fwd: 1, bwd: 1, width: 6.5 });
    expect(snapshot(mergeRoadGraphs([v1, a]))).toEqual(snapshot(graph));
  });
});
