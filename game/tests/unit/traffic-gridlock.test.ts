// Opt-in real-tile soak for junction deadlocks (handover npc.md, "junction gridlock at the centre seam").
// TRAFFIC_TILES_DIR holds roads-<tile>.json files from the farm; TRAFFIC_SOAK_TILES / TRAFFIC_SOAK_SECONDS pick
// the district and length (default: the three centre tiles around the seam junction at engine (145, -15), 10 min).
import { expect, test } from 'vitest';
import fs from 'node:fs';
import { TrafficFlow, bodyOverlap } from '../../src/traffic/flow';
import { TrafficNetwork } from '../../src/traffic/network';
import { trafficRoads } from '../../src/traffic/roads';
import { buildTileTraffic } from '../../src/traffic/tileNetwork';
import { tileBounds } from '../../src/render/lights/tileLights';

const DIR = process.env.TRAFFIC_TILES_DIR;
const TILES = (process.env.TRAFFIC_SOAK_TILES ?? 't0_0_0,t0_1_0,t0_0_-1').split(',');
const SECONDS = +(process.env.TRAFFIC_SOAK_SECONDS ?? 600);

test.skipIf(!DIR)('real tiles: no car stays locked and no two cars overlap', () => {
  const network = new TrafficNetwork();
  network.density = { hourAt: () => 16, multiplier: 1 };
  for (const t of TILES) network.setTile(buildTileTraffic(t, trafficRoads(JSON.parse(fs.readFileSync(`${DIR}/roads-${t}.json`, 'utf8'))), tileBounds(t)!, t));
  const flow = new TrafficFlow(network, 0);
  flow.reset(0);
  let overlaps = 0;
  for (let s = 1; s <= SECONDS * 10; s++) {
    flow.update(s / 10);
    if (s % 5) continue;
    const cars = [...flow.cars.values()];
    for (let i = 0; i < cars.length; i++) for (let j = i + 1; j < cars.length; j++) {
      const a = cars[i], b = cars[j];
      if (Math.abs(a.pose.x - b.pose.x) < 7 && Math.abs(a.pose.z - b.pose.z) < 7 && bodyOverlap(a.pose, a, b.pose, b, -0.05) && !flow.levelApart(a, b)) overlaps++;
    }
  }
  const locked = [...flow.cars.values()].filter(c => c.stuck > 120).map(c => `${c.key} by ${c.blockedBy}`);
  expect(locked).toEqual([]);
  expect(overlaps).toBe(0);
  expect(flow.stats.exited).toBeGreaterThan(SECONDS / 6);
}, 1_800_000);
