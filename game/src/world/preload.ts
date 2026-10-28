import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type { PlayWorld } from '../play';
import { VISUAL_RING_RADIUS } from './residency';
import { worldLoad } from './loadProgress';

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

/** Include TSL texture nodes as well as ordinary PBR map slots. Ground's array textures live in node graphs. */
function modelTextures(scene: THREE.Object3D): Set<THREE.Texture> {
  const textures = new Set<THREE.Texture>();
  scene.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
        else if (value?.isNode) (value as Node).traverse(node => {
          const map = (node as Node & { value?: THREE.Texture }).value;
          if (map?.isTexture) textures.add(map);
        });
      }
    }
  });
  return textures;
}

/** The session has loaded actors/parked cars but has not enabled input or installed its animation loop yet. */
export async function preloadWorld(world: PlayWorld, focus: { x: number; z: number }) {
  const { tiles, streamer, coarse, materials, renderer, scene, camera, env, renderFrame, params } = world;
  tiles.setFocus(focus.x, focus.z, Math.max(VISUAL_RING_RADIUS, params.collisionRadius));
  env.follow(new THREE.Vector3(focus.x, camera.position.y, focus.z));
  worldLoad.update({ phase: 'loading' });
  const started = performance.now();
  let stable = 0;
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
    if (errors.length) throw new Error(`world preload: ${errors.join('; ')}`);
    if (performance.now() - started > 300_000) throw new Error(`world preload timed out: ${JSON.stringify(worldLoad.snapshot())}`);
    stable = tiles.settled && !materials?.ground?.pending && drafts.loaded === drafts.total && collision.loaded === collision.total
      ? stable + 1 : 0;
  }
  // Upload even offscreen textures and compile all currently visible model materials. The temporary culling
  // override includes meshes behind the camera; it never changes the distance-region request set.
  worldLoad.update({ phase: 'compiling', compile: { done: 0, total: 6 } });
  const textures = modelTextures(scene);
  let uploaded = 0;
  for (const texture of textures) {
    renderer.initTexture(texture);
    worldLoad.update({ textures: { loaded: ++uploaded, pending: textures.size - uploaded } });
    if (uploaded % 32 === 0) await frame();
  }
  const culled = new Map<THREE.Object3D, boolean>();
  scene.traverse(object => { culled.set(object, object.frustumCulled); object.frustumCulled = false; });
  const rotation = camera.quaternion.clone();
  try {
    await renderer.compileAsync(scene, camera);
    for (const [object, value] of culled) object.frustumCulled = value;
    culled.clear();
    // Real draws warm the MRT, shadow, GTAO and temporal variants that compileAsync's default target cannot
    // cover. Six views also exercise vegetation's view-dependent instance buffers before control is released.
    const directions = [[0, 0, -1], [1, 0, 0], [0, 0, 1], [-1, 0, 0], [0, 1, 0], [0, -1, 0]];
    for (const [i, direction] of directions.entries()) {
      camera.lookAt(camera.position.clone().add(new THREE.Vector3(...direction)));
      camera.updateMatrixWorld();
      renderFrame();
      await drainGpu(renderer);
      worldLoad.update({ compile: { done: i + 1, total: directions.length },
        textures: { loaded: renderer.info.memory.textures, pending: 0 } });
      await frame();
    }
  } finally {
    for (const [object, value] of culled) object.frustumCulled = value;
    camera.quaternion.copy(rotation);
    camera.updateMatrixWorld();
  }
  // Restore the initial view and flush temporal history from the warmup before showing the world.
  for (let i = 0; i < 8; i++) { renderFrame(); await frame(); }
  await drainGpu(renderer);
  worldLoad.update({ phase: 'ready' });
}
