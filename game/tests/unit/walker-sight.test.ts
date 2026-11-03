import { expect, test } from 'vitest';
import fs from 'node:fs';
import * as THREE from 'three/webgpu';
import { CAR, FOOT, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { CYCLE_EPOCH } from '../../src/core/dayCycle';
import { parseWalkerAsset } from '../../src/world/walkers/asset';
import { Walkers } from '../../src/world/walkers';
import { WalkerMeshes } from '../../src/world/walkers/visuals';
import { RING, inView, type Drawn } from '../../src/world/walkers/ring';
import type { Walker } from '../../src/world/walkers/population';

/** A 200 m pavement grid with a camera standing in it looking along +x. */
function grid(): RoadGraphJson {
  const nodes: number[] = [], edges: number[][] = [];
  const id = (i: number, j: number) => i * 5 + j;
  for (let i = 0; i < 5; i++) for (let j = 0; j < 5; j++) nodes.push(25 + i * 50, 25 + j * 50);
  for (let i = 0; i < 5; i++) for (let j = 0; j < 5; j++) {
    if (i < 4) edges.push([id(i, j), id(i + 1, j), FOOT, 50]);
    if (j < 4) edges.push([id(i, j), id(i, j + 1), FOOT, 50]);
  }
  edges.push([0, 24, CAR | 2 << 3, 280]);
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', revision: 1, bbox: [0, 0, 250, 250], nodes,
    nodeFlags: nodes.filter((_, i) => i % 2 === 0).map(() => 0 as number) as number[], edges } as unknown as RoadGraphJson;
}

test('births are never drawn in plain sight, and lives that end in sight walk on until out of view', async () => {
  const file = fs.readFileSync('public/assets/walkers/walkers.bin');
  const asset = parseWalkerAsset(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength));
  let now = CYCLE_EPOCH + 3_600_000;
  const walkers = new Walkers(new THREE.Group(), { cap: 100, now: () => now, birthHour: () => 13, graphs: () => [grid()],
    raycast: async origin => ({ point: { x: origin.x, y: 0, z: origin.z }, normal: { x: 0, y: 1, z: 0 }, toi: 0, material: 0 }) as never });
  const internal = walkers as unknown as { asset: unknown; meshes: WalkerMeshes; ring: { entries: Map<number, Drawn<Walker>> } };
  internal.asset = asset; internal.meshes = new WalkerMeshes(asset, 100 + RING.margin);
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
  camera.position.set(100, 1.7, 125); camera.lookAt(200, 1.7, 125); camera.updateMatrixWorld();
  const view = { x: 100, z: 125, fx: 1, fz: 0, cosHalf: Math.cos(Math.atan(Math.tan(Math.PI / 6) * 16 / 9) + 0.21) };
  let lingered = 0, drawnInSight = 0;
  const seenBefore = new Set<number>();
  for (let frame = 0; frame < 60 * 240; frame++) {
    now += 1000 / 60;
    walkers.update(1 / 60, camera, frame * 1000 / 60);
    if (frame % 30 === 0) await new Promise(r => setTimeout(r, 0));
    for (const { walker } of internal.ring.entries.values()) {
      const sight = inView(view, walker.x, walker.z) && Math.hypot(walker.x - view.x, walker.z - view.z) < RING.popFree;
      if (sight && frame > 600) {
        drawnInSight++;
        // A walker first drawn while in sight was not just born.
        if (!seenBefore.has(walker.id)) expect(walker.age).toBeGreaterThan(1);
      }
      if (walker.age >= walker.life) {
        lingered++;
        expect(inView(view, walker.x, walker.z) && Math.hypot(walker.x - view.x, walker.z - view.z) < 80).toBe(true);
      }
      seenBefore.add(walker.id);
    }
  }
  expect(drawnInSight).toBeGreaterThan(1000);
  expect(lingered).toBeGreaterThan(0);
  walkers.dispose();
});
