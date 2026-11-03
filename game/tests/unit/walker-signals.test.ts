import { describe, expect, test } from 'vitest';
import { CAR, EVENT, FOOT, NODE, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { CYCLE_EPOCH } from '../../src/core/dayCycle';
import { TRAFFIC } from '../../src/traffic/constants';
import { signalState } from '../../src/traffic/controls';
import { TrafficNetwork } from '../../src/traffic/network';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic } from '../../src/traffic/tileNetwork';
import { buildWalkNetwork } from '../../src/world/walkers/network';
import { Population, WALKER } from '../../src/world/walkers/population';

/** A signalised four-way junction at (125, 125); pavements along both sides of the west arm (z = 118 and 132)
 *  joined by a signalised zebra across it at x = 115. */
function junction(): RoadGraphJson {
  const nodes = [125, 125, 0, 125, 250, 125, 125, 0, 125, 250, 60, 118, 115, 118, 120, 118, 60, 132, 115, 132, 120, 132];
  const car = CAR | 3 << 3;
  const edges = [
    [1, 0, car, 125], [2, 0, car, 125], [3, 0, car, 125], [4, 0, car, 125],
    [5, 6, FOOT, 55], [6, 7, FOOT, 5], [8, 9, FOOT, 55], [9, 10, FOOT, 5], [6, 9, FOOT, 14],
  ];
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2, bbox: [0, 0, 250, 250], nodes,
    nodeFlags: [NODE.SIGNALS, NODE.SEAM, NODE.SEAM, NODE.SEAM, NODE.SEAM, 0, 0, 0, 0, 0, 0],
    edges, edgeIds: edges.map((_, i) => `e${i}`), bits: { highways: ['secondary'] },
    edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
    edgeAttrs: edges.map((_, i) => i < 4 ? [1, 1, 7, 40, 11, 0, 0, 9] : [0, 0, 2.5, 5, 0, 0, 0, 0]),
    edgeEvents: edges.map((_, i) => i === 0 ? [115, EVENT.CROSSING | EVENT.SIGNALISED | EVENT.MARKED, -1] : null),
  } as RoadGraphJson;
}

describe('walkers at signalised crossings', () => {
  test('the crossing chain carries a gate with the drivers\' controller and approach axis', () => {
    const network = buildWalkNetwork([junction()]);
    const gated = network.chains.filter(c => c.gates.length);
    expect(gated).toHaveLength(1);
    const [gate] = gated[0].gates;
    expect(gate).toMatchObject({ signal: 't0_0_0:0', group: 0, half: 3.5 });
    expect(gate.s).toBeCloseTo(7, 1);
    // The drivers on that arm use the same controller.
    const tile = buildTileTraffic('t0_0_0', trafficRoads(junction()), [0, 0, 250, 250], 'fixture');
    expect(tile.lanes.filter(l => l.end.node === 0 && l.signal).map(l => l.signal)).toContain('t0_0_0:0');
  });

  test('walkers wait at the kerb and are never on the carriageway while the drivers there have green', () => {
    const network = buildWalkNetwork([junction()]);
    const net = new TrafficNetwork();
    net.setTile(buildTileTraffic('t0_0_0', trafficRoads(junction()), [0, 0, 250, 250], 'fixture'));
    // The approach crossing the zebra: the west arm's lane into the junction, heading +x.
    const lane = [...net.tiles.values()][0].lanes.find(l => l.end.node === 0 && l.points[l.points.length - 2] > l.points[0])!;
    expect(lane.signal).toBe('t0_0_0:0');
    const population = new Population(network, () => 13);
    let onRoad = 0, onRoadGreen = 0, waiting = 0;
    const start = CYCLE_EPOCH + 3_600_000;
    for (let second = 0; second < 4 * 3600; second += 0.5) {
      const utc = start + second * 1000;
      for (const w of population.near(115, 125, 12, utc)) {
        if (Math.abs(w.x - 115) > 1.6) continue;
        if (Math.abs(w.z - 125) < 3.4) {
          onRoad++;
          if (signalState(lane, (utc - TRAFFIC.epochMs) / 1000) === 'green') onRoadGreen++;
        } else if (!w.moving && Math.abs(w.z - 125) < 3.5 + WALKER.kerb + WALKER.queue + 0.1) waiting++;
      }
    }
    expect(onRoad).toBeGreaterThan(50);
    expect(waiting).toBeGreaterThan(50);
    // Only the few who cross against the light.
    expect(onRoadGreen / onRoad).toBeLessThan(WALKER.jaywalk * 2);
  });

  test('kerb waits keep positions continuous at 60 Hz', () => {
    const population = new Population(buildWalkNetwork([junction()]), () => 13);
    let previous = new Map<number, { x: number; z: number }>(), compared = 0;
    for (let frame = 0; frame < 60 * 300; frame++) {
      const current = population.near(100, 125, 40, CYCLE_EPOCH + 3_600_000 + frame * 1000 / 60);
      for (const w of current) {
        const old = previous.get(w.id);
        if (old) { expect(Math.hypot(w.x - old.x, w.z - old.z)).toBeLessThan(0.075); compared++; }
      }
      previous = new Map(current.map(w => [w.id, { x: w.x, z: w.z }]));
    }
    expect(compared).toBeGreaterThan(5000);
  });
});
