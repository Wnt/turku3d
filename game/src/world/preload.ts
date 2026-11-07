import * as THREE from 'three/webgpu';
import { modelTextures, textureUploads } from '../render/textureUploads';
import type { PlayWorld } from '../play';
import { worldLoad } from './loadProgress';
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

/** A warmup frame that throws (main.ts guards it: the renderer's pass state is restored and the error logged) does not
 *  end the boot or a city switch; the next frame draws again. */
function warmFrame(renderFrame: () => void) {
  try { renderFrame(); } catch (error) { console.error('[preload] a warmup frame threw; continuing', error); }
}

/** The session has loaded actors/parked cars but has not enabled input or installed its animation loop yet. */
export async function preloadWorld(world: PlayWorld, focus: { x: number; z: number }, prepare?: () => Promise<void>, actors: readonly THREE.Object3D[] = []) {
  const { streamer, coarse, materials, renderer, scene, camera, env, renderFrame } = world;
  env.follow(new THREE.Vector3(focus.x, camera.position.y, focus.z));
  worldLoad.update({ phase: 'loading' });
  const downloaded = loading.begin('Nearby world preload');
  const started = performance.now();
  let stable = 0;
  let lastCounts = '';
  while (stable < 3) {
    await frame();
    coarse?.update(focus.x, focus.z, undefined, undefined, false);
    streamer.update(focus.x, focus.z);
    world.loadingScreen.update(false, coarse?.status);
    const collision = streamer.progressAt(focus.x, focus.z);
    const drafts = coarse?.progressAt(focus.x, focus.z) ?? { loaded: 0, total: 0, errors: [] };
    const errors = [...materials?.ground?.errors ?? [], ...collision.errors];
    worldLoad.update({ visual: { loaded: drafts.loaded, total: drafts.total },
      collision: { loaded: collision.loaded, total: collision.total }, textures: { loaded: 0, pending: materials?.ground?.pending ?? 0 }, errors });
    const counts = `${drafts.loaded}/${drafts.total} models · ${collision.loaded}/${collision.total} collision cells`;
    if (counts !== lastCounts) { loading.note(`Nearby preload · ${counts}`); lastCounts = counts; }
    if (errors.length) throw new Error(`world preload: ${errors.join('; ')}`);
    if (performance.now() - started > 600_000) throw new Error(`World generation timed out after 10 minutes: ${coarse?.status}. ${JSON.stringify(worldLoad.snapshot())}`);
    stable = drafts.total > 0 && !materials?.ground?.pending && drafts.loaded === drafts.total && collision.loaded === collision.total
      ? stable + 1 : 0;
  }
  downloaded(`${lastCounts} · ${loadBytes(worldLoad.snapshot().bytesLoaded)}`);
  // Ground and collision for all menu choices stay ready; shader warmup covers the initial view and actors.
  let stage = performance.now();
  await prepare?.();
  worldLoad.update({ timings: { ...worldLoad.snapshot().timings, groundPreparationMs: performance.now() - stage } });
  const total = 1;
  worldLoad.update({ phase: 'compiling', compile: { done: 0, total } });
  const compiled = loading.begin('GPU texture upload and pipeline warmup');
  const culled = new Map<THREE.Object3D, boolean>();
  // The character and own car must be warm even if a menu/save camera faces away from them.
  // Other resident city materials join the paced graphics queue after the menu is ready.
  for (const actor of actors) actor.traverse(object => {
    culled.set(object, object.frustumCulled); object.frustumCulled = false;
  });
  try {
    stage = performance.now();
    // Surface arrays grown while the ring styled swap in first, so the warmup draws and uploads the final ones.
    await materials?.surfacesReady();
    const textures = modelTextures(scene, camera);
    worldLoad.update({ timings: { ...worldLoad.snapshot().timings, textureDiscoveryMs: performance.now() - stage } });
    stage = performance.now();
    let uploaded = 0;
    await Promise.all([...textures].map(async texture => {
      await textureUploads(renderer).enqueue(texture);
      worldLoad.update({ textures: { loaded: ++uploaded, pending: textures.size - uploaded } });
    }));
    worldLoad.update({ timings: { ...worldLoad.snapshot().timings, textureUploadMs: performance.now() - stage } });
    const firstDraw = loading.begin('Initial shader and GPU warmup draw');
    stage = performance.now();
    warmFrame(renderFrame);
    // A warmup frame creates a few new shader modules (render/shaderModules.ts: Dawn translates each on the GPU
    // process's main thread); the draws it turned away take the next frames, so the loading screen keeps moving.
    const modules = (renderer as unknown as { shaderModules?: { takeDeferred(): number } | null }).shaderModules;
    for (let i = 0; i < 900 && modules?.takeDeferred(); i++) { await frame(); warmFrame(renderFrame); }
    // First-use pipelines of the live frame compile asynchronously (render/asyncPipelines.ts): wait for them here,
    // so the menu opens with the initial view's pipelines ready.
    await (renderer as unknown as { pipelinesSettled?: () => Promise<void> }).pipelinesSettled?.();
    await drainGpu(renderer);
    worldLoad.update({ timings: { ...worldLoad.snapshot().timings, pipelineWarmupMs: performance.now() - stage },
      compile: { done: 1, total } });
    firstDraw();
  } finally {
    for (const [object, value] of culled) object.frustumCulled = value;
  }
  // The camera never leaves its initial pose. Two history frames are enough before the live menu
  // loop continues convergence; the old eight-frame flush was needed after panorama camera jumps.
  stage = performance.now();
  for (let i = 0; i < 2; i++) { warmFrame(renderFrame); await frame(); }
  await drainGpu(renderer);
  worldLoad.update({ timings: { ...worldLoad.snapshot().timings, temporalWarmupMs: performance.now() - stage } });
  compiled(`${renderer.info.memory.textures} textures · ${total}/${total} views`);
  worldLoad.update({ phase: 'ready' });
}
