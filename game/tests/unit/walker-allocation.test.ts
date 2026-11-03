import { expect, test } from 'vitest';
import fs from 'node:fs';
import inspector from 'node:inspector';
import * as THREE from 'three/webgpu';
import { FOOT, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { CYCLE_EPOCH } from '../../src/core/dayCycle';
import { parseWalkerAsset } from '../../src/world/walkers/asset';
import { Walkers } from '../../src/world/walkers';
import { WalkerMeshes } from '../../src/world/walkers/visuals';
import { RING } from '../../src/world/walkers/ring';

/** A dense 250 m pavement grid (25 m blocks) under a camera walking along it. */
function grid(): RoadGraphJson {
  const nodes: number[] = [], edges: number[][] = [], n = 11, id = (i: number, j: number) => i * n + j;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) nodes.push(i * 25, j * 25);
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    if (i + 1 < n) edges.push([id(i, j), id(i + 1, j), FOOT, 25]);
    if (j + 1 < n) edges.push([id(i, j), id(i, j + 1), FOOT, 25]);
  }
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 1, bbox: [0, 0, 250, 250], nodes,
    nodeFlags: nodes.filter((_, i) => i % 2 === 0).map(() => 0), edges } as unknown as RoadGraphJson;
}

test('the walker frame path stays light on allocation (people drawn, reacting and crowding)', async () => {
  const file = fs.readFileSync('public/assets/walkers/walkers.bin');
  const asset = parseWalkerAsset(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength));
  let now = CYCLE_EPOCH + 3_600_000;
  const graphs = [grid()];
  const player = { x: 125, y: 0, z: 125, vx: 0, vz: -1.4, car: null };
  const walkers = new Walkers(new THREE.Group(), { cap: 60, now: () => now, birthHour: () => 13, graphs: () => graphs, threats: () => [player],
    raycast: async origin => ({ point: { x: origin.x, y: 0, z: origin.z }, normal: { x: 0, y: 1, z: 0 }, toi: 0, material: 0, tile: null }) as never });
  const internal = walkers as unknown as { asset: unknown; meshes: WalkerMeshes };
  internal.asset = asset; internal.meshes = new WalkerMeshes(asset, 60 + RING.margin);
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
  const frame = (k: number) => {
    now += 1000 / 60;
    player.z = 200 - (k / 60) * 1.4;
    camera.position.set(125, 1.7, player.z + 4); camera.lookAt(125, 1.5, player.z - 20); camera.updateMatrixWorld();
    walkers.update(1 / 60, camera, k * 1000 / 60);
  };
  for (let k = 0; k < 600; k++) { frame(k); if (k % 30 === 0) await new Promise(r => setTimeout(r, 0)); }
  expect(walkers.snapshot().drawn).toBeGreaterThan(20);
  const session = new inspector.Session(); session.connect();
  const post = (method: string, params: object = {}) => new Promise<any>((res, rej) => session.post(method, params, (e, r) => e ? rej(e) : res(r)));
  await post('HeapProfiler.startSampling', { samplingInterval: 256, includeObjectsCollectedByMajorGC: true, includeObjectsCollectedByMinorGC: true });
  const frames = 600;
  for (let k = 600; k < 600 + frames; k++) frame(k);
  const { profile } = await post('HeapProfiler.stopSampling');
  session.disconnect();
  let walkerBytes = 0;
  const walk = (node: { callFrame: { url: string }; selfSize: number; children: unknown[] }) => {
    if (/\/world\/walkers\/|populationFocus/.test(node.callFrame.url)) walkerBytes += node.selfSize;
    for (const child of node.children) walk(child as typeof node);
  };
  walk(profile.head);
  // 2026-11-03: 134 KB/frame before the scratch/pool pass, about 35 KB after (new lives' routes, the 400 ms scan's
  // new lives, and part of the draw loop remain). Guard the gain; lower the bound as the remainder goes.
  expect(walkerBytes / frames).toBeLessThan(Number(process.env.WALKER_BYTES_PER_FRAME ?? 60_000));
  console.log(`walker code allocated ${(walkerBytes / frames).toFixed(0)} B/frame, ${walkers.snapshot().drawn} drawn`);
}, 120_000);
