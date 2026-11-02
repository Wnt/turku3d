/** Local crowd studies use the game's population/renderer in the already-loaded QA world. */
import * as THREE from 'three/webgpu';
import { Walkers } from '../world/walkers';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import { WalkerMeshes, type WalkerInstance } from '../world/walkers/visuals';
import { HUMAN_MODELS, appearanceForModel } from '../world/walkers/appearance';
import { loadWalkerAsset } from '../world/walkers/asset';

interface StudyWorld {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  graphs: () => readonly RoadGraphJson[];
}

export async function createHumanStudy(world: StudyWorld, url: string) {
  const ray = new THREE.Raycaster();
  const ground: THREE.Object3D[] = [];
  world.scene.traverse(o => {
    if (!(o instanceof THREE.Mesh)) return;
    if (o.name === 'ground-batch' || o.userData.layer === 'ground' || o.userData.layer === 'roads') ground.push(o);
  });
  let utc = Date.UTC(2026, 6, 1, 10), animate = false;
  const walkers = new Walkers(world.scene, {
    cap: 100, now: () => utc, birthHour: () => 13, graphs: world.graphs,
    raycast: async (origin, direction, maxToi) => {
      ray.set(new THREE.Vector3(origin.x, origin.y, origin.z), new THREE.Vector3(direction.x, direction.y, direction.z));
      ray.far = maxToi;
      const hit = ray.intersectObjects(ground, false)[0];
      if (!hit) return null;
      return { point: { x: hit.point.x, y: hit.point.y, z: hit.point.z }, toi: hit.distance,
        normal: { x: 0, y: 1, z: 0 }, material: 0 } as never;
    },
  });
  await walkers.load(url);
  return {
    walkers,
    frame: (dt = 1 / 60) => { if (animate) utc += dt * 1000; walkers.update(dt, world.camera, performance.now()); },
    setTime: (time: number) => { utc = time; },
    setAnimate: (on: boolean) => { animate = on; },
    dispose: () => walkers.dispose(),
  };
}

/** A reproducible lineup at a surveyed street location, useful for comparing the same bodies at 2–15 m. */
export async function createHumanLineup(world: StudyWorld, url: string, origin: [number, number, number], count = 8) {
  const asset = await loadWalkerAsset(url), meshes = new WalkerMeshes(asset, count);
  const ground: THREE.Object3D[] = [];
  world.scene.traverse(o => { if (o instanceof THREE.Mesh && (o.name === 'ground-batch' || ['ground', 'roads'].includes(o.userData.layer))) ground.push(o); });
  const ray = new THREE.Raycaster();
  const positions = Array.from({ length: count }, (_, i) => {
    const x = origin[0] + (i - (count - 1) / 2) * .9;
    ray.set(new THREE.Vector3(x, origin[1] + 1, origin[2]), new THREE.Vector3(0, -1, 0)); ray.far = 2;
    return { x, y: ray.intersectObjects(ground, false)[0]?.point.y ?? origin[1] };
  });
  world.scene.add(meshes.group);
  let time = 0;
  const render = (seconds = time) => {
    time = seconds;
    const instances: WalkerInstance[] = Array.from({ length: count }, (_, i) => {
      const model = HUMAN_MODELS[(i * 9) % HUMAN_MODELS.length];
      const variant = asset.variants.length === 12 ? model.family : i % asset.variants.length, v = asset.variants[variant];
      const look = appearanceForModel(model, i * 1337);
      return { variant, ...positions[i], z: origin[2], yaw: 0,
        scale: asset.variants.length === 12 ? model.height : 0.96 + (i % 3) * 0.04, shape: asset.variants.length === 12 ? model.shape : undefined, rowStart: v.rowBase + v.walk.row, frames: v.walk.frames,
        phase: (seconds / 1.333 + i * 0.17) % 1, previousPhase: (seconds / 1.333 + i * 0.17) % 1,
        skin: asset.variants.length === 12 ? look.skin : 0xeed2be, top: look.top, bottom: look.bottom, hair: asset.variants.length === 12 ? look.hair : [0x816643, 0x30281e, 0xaaa493][i % 3],
        shoes: look.shoes, inner: look.inner, legwear: look.legwear, fade: 1 };
    });
    meshes.update(instances);
  };
  render();
  return { meshes, render, dispose: () => meshes.dispose() };
}

if (import.meta.hot) import.meta.hot.accept();
