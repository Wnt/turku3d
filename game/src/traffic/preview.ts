// Fixed-camera traffic evidence on the already loaded QA world. Loaded explicitly through CDP; never started
// by the game. Uses production car batches/road data and the same TrafficFlow as the physics worker.
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals, type TrafficInstance } from '../actors/car';
import type { CoarseTiles } from '../world/coarseTiles';
import type { Manifest } from '../world/manifest';
import { CHASSIS_LIFT, PAINT_WEIGHTS, TRAFFIC_MODELS, TRAFFIC_PAINTS } from './constants';
import { hash01, pick } from './hash';
import { TrafficNetwork } from './network';
import { TrafficFlow, sampleFlowPose } from './flow';
import { trafficRoads } from './roads';
import { buildTileTraffic } from './tileNetwork';
import { tileBounds } from '../render/lights/tileLights';
import type { CarPose } from './network';

function chassisRotation(p: CarPose) {
  const yaw = Math.atan2(-p.hz, p.hx), sy = Math.sin(yaw / 2), cy = Math.cos(yaw / 2);
  const sp = Math.sin(p.pitch / 2), cp = Math.cos(p.pitch / 2);
  return { x: sy * sp, y: sy * cp, z: cy * sp, w: cy * cp };
}

export async function startTrafficPreview() {
  const game = window.__game!, render = window.__render!;
  const lights = game.lights as { source: CoarseTiles; posts: { mesh: THREE.Object3D } };
  const source = lights.source, scene = lights.posts.mesh.parent!;
  const root = new THREE.Group(); root.name = 'traffic-evidence'; scene.add(root);
  const manifest = (source as unknown as { host: { manifest: Manifest } }).host.manifest;
  const visuals = new ParkedCarVisuals(root);
  visuals.reserveTraffic(160);
  await visuals.load({ ...manifest, vehicles: [] });
  const network = new TrafficNetwork();
  network.density = { hourAt: () => 16, multiplier: 1 };
  let flow = new TrafficFlow(network), at = 0, running = false, mode = 'flow', last = performance.now(), cap = 30;
  const updateTiles = () => {
    for (const [id, entry] of source.entries) if (entry.roads && !network.tiles.has(id)) {
      network.setTile(buildTileTraffic(id, trafficRoads(entry.roads), tileBounds(id)!, 'evidence'));
    }
  };
  updateTiles();
  // Pin one road-data footprint for an A/B, independent of asynchronous visual residency.
  const ci = Math.floor(render.camera.position.x / 250), cj = Math.floor(render.camera.position.z / 250);
  const tileIds = Array.from({ length: 9 }, (_, k) => `t0_${ci - 1 + k % 3}_${cj - 1 + Math.floor(k / 3)}`);
  const roads = await Promise.all(tileIds.map(async id => {
    const response = await fetch(`/coarse/${id}/roads.json`);
    if (!response.ok) throw new Error(`Traffic evidence roads ${id}: ${response.status}`);
    return { id, json: await response.json() };
  }));
  for (const { id, json } of roads) network.setTile(buildTileTraffic(id, trafficRoads(json), tileBounds(id)!, 'evidence'));
  const height = (lane: import('./tileNetwork').Lane, s: number) => {
    if (!lane.ys) return null;
    let i = 1; while (i < lane.cum.length - 1 && lane.cum[i] < s) i++;
    const f = Math.max(0, Math.min(1, (s - lane.cum[i - 1]) / Math.max(0.01, lane.cum[i] - lane.cum[i - 1])));
    return lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f;
  };
  const draw = () => {
    const instances: TrafficInstance[] = [];
    if (mode === 'flow') {
      flow.update(at);
      for (const car of flow.cars.values()) {
        const p = sampleFlowPose(car, 0, height);
        if (!Number.isFinite(p.y)) continue;
        instances.push({ model: TRAFFIC_MODELS[car.model], color: TRAFFIC_PAINTS[pick(hash01('paint', car.key), PAINT_WEIGHTS)],
          x: p.x, y: p.y + CHASSIS_LIFT, z: p.z, q: chassisRotation(p) });
      }
    } else {
      network.enumerate(render.camera.position.x, render.camera.position.z, 250, at, (lane, n) => {
        const p = network.pose(lane, n, at, height), key = network.key(lane, n);
        if (!Number.isFinite(p.y)) return;
        instances.push({ model: TRAFFIC_MODELS[pick(hash01('model', key), [0.4, 0.25, 0.22, 0.13])],
          color: TRAFFIC_PAINTS[pick(hash01('paint', key), PAINT_WEIGHTS)], x: p.x, y: p.y + CHASSIS_LIFT, z: p.z, q: chassisRotation(p) });
      });
    }
    instances.sort((a, b) => Math.hypot(a.x - render.camera.position.x, a.z - render.camera.position.z)
      - Math.hypot(b.x - render.camera.position.x, b.z - render.camera.position.z));
    instances.length = Math.min(instances.length, cap);
    visuals.setTraffic(instances); visuals.update([]);
    return instances.length;
  };
  const api = {
    network, get flow() { return flow; }, root,
    setCap(value: number) { cap = Math.max(0, Math.min(160, value)); draw(); },
    setMode(value: 'flow' | 'baseline') { mode = value; draw(); },
    seek(t: number) {
      at = t;
      if (mode === 'flow') {
        if (t < flow.tick * 0.1) flow.reset(Math.floor(t / 3600) * 3600 - 90);
        flow.update(t);
      }
      return draw();
    },
    play(value = true) { running = value; last = performance.now(); },
    snapshot() { return { at, mode, lanes: [...network.tiles.values()].reduce((n, tile) => n + tile.lanes.length, 0),
      drawn: draw(), flow: flow.stats, cars: flow.cars.size }; },
    replace(Flow: typeof TrafficFlow) { flow = new Flow(network); flow.reset(0); flow.update(at); draw(); },
    dispose() { running = false; render.beforeFrame = undefined; root.removeFromParent(); },
  };
  render.beforeFrame = () => {
    const now = performance.now();
    if (running) { at += Math.min(0.1, (now - last) / 1000); draw(); }
    last = now;
    return true;
  };
  (game as Record<string, unknown>).trafficPreview = api;
  draw();
  return api.snapshot();
}

if (import.meta.hot) {
  import.meta.hot.accept('./flow', next => {
    if (next) (window.__game?.trafficPreview as { replace: (Flow: typeof TrafficFlow) => void } | undefined)?.replace(next.TrafficFlow);
  });
  import.meta.hot.accept(next => {
    const old = window.__game?.trafficPreview as { snapshot: () => { at: number }; dispose: () => void; seek: (t: number) => void } | undefined;
    const at = old?.snapshot().at ?? 0;
    old?.dispose();
    if (next && window.__game) void next.startTrafficPreview().then(() =>
      (window.__game!.trafficPreview as { seek: (t: number) => void }).seek(at));
  });
}
