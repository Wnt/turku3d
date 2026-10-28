import * as THREE from 'three/webgpu';
import { modelTextures, textureUploads } from '../render/textureUploads';
import type { PlayWorld } from '../play';
import { VISUAL_RING_RADIUS } from './residency';
import { worldLoad } from './loadProgress';
import type { V3 } from '../core/origin';
import { loading, loadBytes } from '../core/loading';

const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));

/** r186 removed Renderer.waitForGPU; wait for the actual submitted work on each backend. */
async function drainGpu(renderer: THREE.WebGPURenderer) {
  const backend = renderer.backend as unknown as {
    device?: { queue: { onSubmittedWorkDone(): Promise<void> } }; gl?: WebGL2RenderingContext;
  };
  if (backend.device) return backend.device.queue.onSubmittedWorkDone();
  const gl = backend.gl!;
  const fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
  if (!fence) throw new Error('preload GPU fence could not be created');
  gl.flush();
  try {
    for (;;) {
      const status = gl.clientWaitSync(fence, 0, 0);
      if (status === gl.WAIT_FAILED) throw new Error('preload GPU fence failed');
      if (status !== gl.TIMEOUT_EXPIRED) return;
      await frame();
    }
  } finally { gl.deleteSync(fence); }
}

/** The session has loaded actors/parked cars but has not enabled input or installed its animation loop yet. */
export async function preloadWorld(world: PlayWorld, focus: { x: number; z: number }, prepare?: () => Promise<V3[]>) {
  const { tiles, streamer, coarse, materials, renderer, scene, camera, env, renderFrame, params } = world;
  tiles.setFocus(focus.x, focus.z, Math.max(VISUAL_RING_RADIUS, params.collisionRadius));
  env.follow(new THREE.Vector3(focus.x, camera.position.y, focus.z));
  worldLoad.update({ phase: 'loading' });
  const downloaded = loading.begin('360° world preload');
  const started = performance.now();
  let stable = 0;
  let lastCounts = '';
  while (stable < 3) {
    await frame();
    coarse?.update(focus.x, focus.z);
    streamer.update(focus.x, focus.z);
    tiles.update(camera);
    const collision = streamer.progressAt(focus.x, focus.z);
    const drafts = coarse?.progressAt(focus.x, focus.z) ?? { loaded: 0, total: 0, errors: [] };
    const visual = tiles.progress;
    const errors = [...tiles.errors, ...materials?.ground?.errors ?? [], ...collision.errors, ...drafts.errors];
    worldLoad.update({ visual: { loaded: visual.loaded + drafts.loaded, total: visual.total + drafts.total },
      collision: { loaded: collision.loaded, total: collision.total }, textures: { loaded: 0, pending: materials?.ground?.pending ?? 0 }, errors });
    const counts = `${visual.loaded + drafts.loaded}/${visual.total + drafts.total} models · ${collision.loaded}/${collision.total} collision cells`;
    if (counts !== lastCounts) { loading.note(`360° preload · ${counts}`); lastCounts = counts; }
    if (errors.length) throw new Error(`world preload: ${errors.join('; ')}`);
    if (performance.now() - started > 300_000) throw new Error(`world preload timed out: ${JSON.stringify(worldLoad.snapshot())}`);
    stable = tiles.settled && !materials?.ground?.pending && drafts.loaded === drafts.total && collision.loaded === collision.total
      ? stable + 1 : 0;
  }
  downloaded(`${lastCounts} · ${loadBytes(worldLoad.snapshot().bytesLoaded)}`);
  // Upload even offscreen textures and compile all currently visible model materials. The temporary culling
  // override includes meshes behind the camera; it never changes the distance-region request set.
  const starts = await prepare?.() ?? [world.spawn];
  const total = starts.length * 6;
  worldLoad.update({ phase: 'compiling', compile: { done: 0, total } });
  const compiled = loading.begin('GPU texture upload and pipeline warmup');
  const textures = modelTextures(scene);
  let uploaded = 0;
  await Promise.all([...textures].map(async texture => {
    await textureUploads(renderer).enqueue(texture);
    worldLoad.update({ textures: { loaded: ++uploaded, pending: textures.size - uploaded } });
  }));
  const culled = new Map<THREE.Object3D, boolean>();
  scene.traverse(object => { culled.set(object, object.frustumCulled); object.frustumCulled = false; });
  const rotation = camera.quaternion.clone(), position = camera.position.clone();
  const offset = position.clone().sub(new THREE.Vector3(world.spawn.x, world.spawn.y, world.spawn.z));
  try {
    // Real pass draws compile the same pipelines used by play, including offscreen meshes, reflections
    // and CSM. compileAsync yields for every draw and repeats this work in several mismatched targets.
    const firstDraw = loading.begin('Initial shader and GPU warmup draw');
    renderFrame();
    await drainGpu(renderer);
    firstDraw();
    for (const [object, value] of culled) object.frustumCulled = value;
    culled.clear();
    // Real draws warm the MRT, shadow, GTAO and temporal variants that compileAsync's default target cannot
    // cover. Six views also exercise vegetation's view-dependent instance buffers before control is released.
    const directions = [[0, 0, -1], [1, 0, 0], [0, 0, 1], [-1, 0, 0], [0, 1, 0], [0, -1, 0]];
    let done = 0;
    for (const start of starts) {
      camera.position.set(start.x, start.y, start.z).add(offset);
      env.follow(new THREE.Vector3(start.x, start.y, start.z));
      for (const direction of directions) {
        camera.lookAt(camera.position.clone().add(new THREE.Vector3(...direction)));
        camera.updateMatrixWorld();
        renderFrame();
        await drainGpu(renderer);
        worldLoad.update({ compile: { done: ++done, total },
          textures: { loaded: renderer.info.memory.textures, pending: 0 } });
        loading.note(`GPU warmup ${done}/${total} directions · ${renderer.info.memory.textures} textures resident`);
        await frame();
      }
    }
  } finally {
    for (const [object, value] of culled) object.frustumCulled = value;
    camera.position.copy(position);
    env.follow(new THREE.Vector3(world.spawn.x, world.spawn.y, world.spawn.z));
    camera.quaternion.copy(rotation);
    camera.updateMatrixWorld();
  }
  // Restore the initial view and flush temporal history from the warmup before showing the world.
  for (let i = 0; i < 8; i++) { renderFrame(); await frame(); }
  await drainGpu(renderer);
  compiled(`${renderer.info.memory.textures} textures · ${total}/${total} directions`);
  worldLoad.update({ phase: 'ready' });
}
