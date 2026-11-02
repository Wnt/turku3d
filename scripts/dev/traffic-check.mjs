#!/usr/bin/env node
// Run against a frozen standalone traffic bundle on a CPU host, with no Vite, renderer or npm install.
import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modulePath = process.env.TRAFFIC_SIM_MODULE;
if (!modulePath) throw new Error('Set TRAFFIC_SIM_MODULE to the standalone traffic-sim.mjs bundle');
const { TrafficNetwork, TrafficFlow, trafficRoads, buildTileTraffic, sampleFlowPose, interpolateFlowPose } =
  await import(pathToFileURL(path.resolve(modulePath)).href);

function network(signals = true, lanes = 2) {
  const json = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 2,
    bbox: [0, 0, 250, 250], nodes: [125, 125, 0, 125, 250, 125, 125, 0, 125, 250],
    nodeFlags: [signals ? 2 : 0, 32, 32, 32, 32],
    edges: [1, 2, 3, 4].map(n => [n, 0, 27, 125]), edgeIds: ['west', 'east', 'north', 'south'],
    bits: { highways: ['secondary'] },
    edgeAttrColumns: ['lanes_fwd', 'lanes_bwd', 'width', 'kmh', 'car_density', 'highway', 'setback0', 'setback1'],
    edgeAttrs: Array.from({ length: 4 }, () => [lanes, lanes, lanes * 7, 40, 11, 0, 0, 9]) };
  const net = new TrafficNetwork();
  net.density = { hourAt: () => 16, multiplier: 1 };
  net.setTile(buildTileTraffic('t0_0_0', trafficRoads(json), json.bbox, 'fixture'));
  return net;
}

test('same explicit epoch, late checkpoint join, clock rewind and streaming preserve deterministic drivers', () => {
  const a = new TrafficFlow(network(), 3590), b = new TrafficFlow(network(), 3590);
  a.update(3599); a.update(3601); b.update(3601);
  assert.deepEqual(a.checkpoint(), b.checkpoint());
  const late = new TrafficFlow(network()); late.restore(a.checkpoint());
  a.update(3620); late.update(3620);
  assert.deepEqual(a.checkpoint(), late.checkpoint());
  a.update(3601);
  assert.deepEqual(a.checkpoint(), b.checkpoint());
  const before = b.checkpoint();
  b.network.revision++;
  b.update(3601);
  assert.deepEqual(b.checkpoint(), before);
});

test('a claim removes the driver and its reservation, including after checkpoint restore', () => {
  const flow = new TrafficFlow(network(), 0); flow.update(60);
  const car = [...flow.cars.values()].find(c => c.reservation) ?? [...flow.cars.values()][0];
  assert.ok(car);
  flow.suppress(car.key);
  assert.equal(flow.cars.has(car.key), false);
  const restored = new TrafficFlow(network()); restored.restore(flow.checkpoint()); restored.update(65);
  assert.equal(restored.cars.has(car.key), false);
});

for (const signals of [false, true]) test(`600 seconds: no tick/interpolated overlaps, all approaches progress, no starvation (signals=${signals})`,
  { timeout: 180000 }, () => {
    const net = network(signals), flow = new TrafficFlow(net, 0);
    flow.reset(0);
    let longestWait = 0, samples = 0;
    const served = new Map(), seen = new Set();
    for (let k = 1; k <= 6000; k++) {
      flow.update(k / 10);
      const cars = [...flow.cars.values()];
      for (const car of cars) {
        longestWait = Math.max(longestWait, car.waiting);
        if (car.s > car.lane.length && !seen.has(car.key)) {
          seen.add(car.key); const origin = car.key.split(':')[1]; served.set(origin, (served.get(origin) ?? 0) + 1);
        }
      }
      for (const fraction of [0, 0.5, 1]) {
        const poses = cars.map(car => ({ ...interpolateFlowPose(car, fraction, () => 0), length: car.length, width: car.width, key: car.key }));
        for (let i = 0; i < poses.length; i++) for (let j = i + 1; j < poses.length; j++) {
          const a = poses[i], b = poses[j];
          if (Math.abs(a.x - b.x) > 7 || Math.abs(a.z - b.z) > 7) continue;
          assert.equal(overlap(a, b), false, `overlap at t=${k / 10} fraction=${fraction}: ${a.key}/${b.key}`);
          samples++;
        }
      }
    }
    assert.ok(flow.stats.exited > 50, `only ${flow.stats.exited} exits`);
    assert.equal(served.size, 8, `only ${served.size}/8 incoming lanes served`);
    assert.ok(longestWait < 90, `maximum wait ${longestWait.toFixed(1)}s`);
    assert.ok(samples > 100);
    console.log(JSON.stringify({ signals, exits: flow.stats.exited, longestWait, served: Object.fromEntries(served), footprintChecks: samples }));
  });

function overlap(a, b) {
  for (const [x, z] of [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]) {
    const ra = Math.abs(a.hx * x + a.hz * z) * a.length / 2 + Math.abs(-a.hz * x + a.hx * z) * a.width / 2;
    const rb = Math.abs(b.hx * x + b.hz * z) * b.length / 2 + Math.abs(-b.hz * x + b.hx * z) * b.width / 2;
    if (Math.abs((b.x - a.x) * x + (b.z - a.z) * z) >= ra + rb - 0.01) return false;
  }
  return true;
}
