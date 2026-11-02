// Fixed-camera traffic evidence on the already loaded QA world. Loaded explicitly through CDP; never started
// by the game. Uses production car batches/road data and the same TrafficFlow as the physics worker.
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals, type TrafficInstance } from '../actors/car';
import type { CoarseTiles } from '../world/coarseTiles';
import type { Manifest } from '../world/manifest';
import { CHASSIS_LIFT, PAINT_WEIGHTS, TRAFFIC_MODELS, TRAFFIC_PAINTS } from './constants';
import { hash01, hash32, pick } from './hash';
import { TrafficNetwork } from './network';
import { FLOW_STEP, TrafficFlow, interpolateFlowPose } from './flow';
import { vehicleLights } from './lights';
import { trafficRoads } from './roads';
import { buildTileTraffic } from './tileNetwork';
import { tileBounds } from '../render/lights/tileLights';
import type { CarPose } from './network';
import { TrafficSignals } from './signals';

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
  const signals = new TrafficSignals(root);
  const manifest = (source as unknown as { host: { manifest: Manifest } }).host.manifest;
  const visuals = new ParkedCarVisuals(root);
  visuals.reserveTraffic(160);
  await visuals.load({ ...manifest, vehicles: [] });
  const network = new TrafficNetwork();
  network.density = { hourAt: () => 16, multiplier: 1 };
  let flow = new TrafficFlow(network), at = 0, running = false, mode = 'flow', last = performance.now(), cap = 30;
  // Pin one road-data footprint for an A/B, independent of asynchronous visual residency.
  const ci = Math.floor(render.camera.position.x / 250), cj = Math.floor(render.camera.position.z / 250);
  const tileIds = Array.from({ length: 9 }, (_, k) => `t0_${ci - 1 + k % 3}_${cj - 1 + Math.floor(k / 3)}`);
  const roads = await Promise.all(tileIds.map(async id => {
    const response = await fetch(`/coarse/${id}/roads.json`);
    if (!response.ok) throw new Error(`Traffic evidence roads ${id}: ${response.status}`);
    const text = await response.text();
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    const hash = [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, '0')).join('');
    return { id, json: JSON.parse(text), hash };
  }));
  for (const { id, json, hash } of roads) {
    network.setTile(buildTileTraffic(id, trafficRoads(json), tileBounds(id)!, hash));
    signals.setTile(id, json);
  }
  const height = (lane: import('./tileNetwork').Lane, s: number) => {
    if (!lane.ys) return null;
    let i = 1; while (i < lane.cum.length - 1 && lane.cum[i] < s) i++;
    const f = Math.max(0, Math.min(1, (s - lane.cum[i - 1]) / Math.max(0.01, lane.cum[i] - lane.cum[i - 1])));
    return lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f;
  };
  const draw = () => {
    signals.update(at);
    const instances: TrafficInstance[] = [];
    if (mode === 'flow') {
      flow.update(at);
      for (const car of flow.cars.values()) {
        const p = interpolateFlowPose(car, (at - flow.tick * FLOW_STEP) / FLOW_STEP, height);
        if (!Number.isFinite(p.y)) continue;
        instances.push({ seed: String(hash32('appearance', car.key)), model: TRAFFIC_MODELS[car.model], color: TRAFFIC_PAINTS[pick(hash01('paint', car.key), PAINT_WEIGHTS)],
          x: p.x, y: p.y + CHASSIS_LIFT, z: p.z, q: chassisRotation(p), ...vehicleLights(car, at) });
      }
    } else {
      network.enumerate(render.camera.position.x, render.camera.position.z, 250, at, (lane, n) => {
        const p = network.pose(lane, n, at, height), key = network.key(lane, n);
        if (!Number.isFinite(p.y)) return;
        instances.push({ seed: String(hash32('appearance', key)), model: TRAFFIC_MODELS[pick(hash01('model', key), [0.4, 0.25, 0.22, 0.13])],
          color: TRAFFIC_PAINTS[pick(hash01('paint', key), PAINT_WEIGHTS)], x: p.x, y: p.y + CHASSIS_LIFT, z: p.z, q: chassisRotation(p) });
      });
    }
    const forward = render.camera.getWorldDirection(new THREE.Vector3());
    const score = (car: TrafficInstance) => {
      const dx = car.x - render.camera.position.x, dz = car.z - render.camera.position.z, d = Math.hypot(dx, dz);
      return d * ((dx * forward.x + dz * forward.z) / Math.max(1, d) > 0.3 ? 1 : 4);
    };
    instances.sort((a, b) => score(a) - score(b));
    instances.length = Math.min(instances.length, cap);
    visuals.setView(render.camera, 0); visuals.setTraffic(instances); visuals.update([]);
    return instances.length;
  };
  const api = {
    network, tileIds, get flow() { return flow; }, root, signals,
    setCap(value: number) { cap = Math.max(0, Math.min(160, value)); draw(); },
    setMode(value: 'flow' | 'baseline') { mode = value; draw(); },
    seek(t: number) {
      at = t;
      if (mode === 'flow') {
        if (t < flow.tick * 0.1) flow.reset(0);
        flow.update(t);
      }
      return draw();
    },
    reset(epoch = 0) { at = epoch; flow.reset(epoch); draw(); },
    play(value = true) { running = value; last = performance.now(); },
    snapshot() { const drawn = draw(), state = flow.checkpoint(); return { at, mode, lanes: [...network.tiles.values()].reduce((n, tile) => n + tile.lanes.length, 0),
      drawn, flow: flow.stats, cars: flow.cars.size, signals: signals.count,
      epoch: state.epoch, stateHash: hash32(JSON.stringify(state)),
      roads: roads.map(r => [r.id, r.hash]), pose: window.__qaPose, hour: (game.dayClock as { hour: number }).hour }; },
    replace(Flow: typeof TrafficFlow) { flow = new Flow(network); flow.reset(0); flow.update(at); draw(); },
    dispose() { running = false; render.beforeFrame = undefined; signals.dispose(); visuals.dispose(); root.removeFromParent(); },
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
