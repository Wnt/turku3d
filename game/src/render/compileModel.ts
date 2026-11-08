import type * as THREE from 'three/webgpu';
import { addNodeBuilderHook } from './nodeBuilderHooks';
import { PreparationBudget } from './preparationBudget';
import { streamTrace } from '../telemetry/streamTrace';

type RenderContexts = { get(target: THREE.RenderTarget | null, mrt: ReturnType<THREE.WebGPURenderer['getMRT']>, depth?: number): unknown };

/** r186 increments this before rendering a scene. Capture a nested pass before its render() call. */
export const nextRenderDepth = (renderer: THREE.WebGPURenderer) =>
  (renderer as unknown as { _callDepth: number })._callDepth + 1;

/** Image-producing nodes must not redraw the resident world from compileAsync's update callbacks. */
export const isPrecompiling = (renderer: THREE.WebGPURenderer) =>
  (renderer as unknown as { _isPreCompiling: boolean })._isPreCompiling === true;

const budgets = new WeakMap<THREE.WebGPURenderer, PreparationBudget>();
/** Streaming shader/pipeline compiles so far and their wall time (ms), for the capture trace; `calls` compileModel draws
 *  handed to r186, `waitMs` the time compileModel spent waiting for its next budget window (graphics queue diagnostics). */
export const compileStats = { count: 0, ms: 0, calls: 0, waitMs: 0, builds: 0, buildMs: 0, hitMs: 0 };
const waitFor = async (pending: Promise<void>) => { const t = performance.now(); await pending; compileStats.waitMs += performance.now() - t; };

/** r186 compileAsync(object, camera, scene) walks the whole world with traverseVisible for its lights on every call,
 * and streaming preparation calls it once per mesh, material and pass. The light set is fixed at startup
 * (environment.ts, vehicle beams), so one walk per rendered frame yields the same list for every compile. */
const lightWalks = new WeakMap<THREE.Object3D, { frame: number; lights: THREE.Object3D[] }>();
export function visibleLights(renderer: THREE.WebGPURenderer, scene: THREE.Object3D): readonly THREE.Object3D[] {
  const frame = renderer.info.frame;
  let walk = lightWalks.get(scene);
  if (!walk || walk.frame !== frame) {
    const lights: THREE.Object3D[] = [];
    scene.traverseVisible(object => { if ((object as THREE.Light).isLight) lights.push(object); });
    walk = { frame, lights };
    lightWalks.set(scene, walk);
  }
  return walk.lights;
}

/** What r186's program key reads from a draw beyond the pass: material, object kind, shadow flags and vertex layout.
 * Draws with equal signatures share a program, so one compile covers them; null for object-specific keys (an
 * unshared InstancedMesh, morph targets, batches). A missed difference only costs a build at first draw. */
export function programSignature(mesh: THREE.Mesh, material: THREE.Material): string | null {
  const instanced = mesh as THREE.InstancedMesh, other = mesh as unknown as { isBatchedMesh?: boolean; count?: number };
  if ((instanced.isInstancedMesh && mesh.userData.sharedInstanceProgram !== true) || mesh.morphTargetInfluences
    || other.isBatchedMesh || (!instanced.isInstancedMesh && (other.count ?? 1) > 1)) return null;
  const geometry = mesh.geometry, skinned = mesh as THREE.SkinnedMesh;
  const layout = Object.keys(geometry.attributes).sort().map(name => {
    const attribute = geometry.attributes[name] as THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
    const interleaved = (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
      ? `${(attribute as THREE.InterleavedBufferAttribute).data.stride}/${(attribute as THREE.InterleavedBufferAttribute).offset}` : '';
    return `${name}:${attribute.itemSize}:${attribute.normalized}:${attribute.array.constructor.name}:${interleaved}`;
  }).join(',');
  return [material.uuid, instanced.isInstancedMesh ? `i${instanced.instanceMatrix.usage}:${instanced.instanceColor?.usage ?? '-'}` : '',
    skinned.isSkinnedMesh ? `s${skinned.skeleton?.bones.length}` : '', mesh.receiveShadow, mesh.castShadow,
    geometry.index?.array.constructor.name ?? 'none', Object.keys(geometry.morphAttributes).join('+'), layout].join('|');
}

/** Compile hidden draws with stable pass state across r186's asynchronous shader stages. */
export async function compileModel(renderer: THREE.WebGPURenderer, model: THREE.Object3D, scene: THREE.Scene,
  camera: THREE.Camera, target: THREE.RenderTarget | null, mrt: ReturnType<THREE.WebGPURenderer['getMRT']> | null,
  context: THREE.WebGPURenderer['contextNode'], opaqueOnly = false, passDepth = 0, override: THREE.Material | null = null,
  castersOnly = false) {
  const contexts = (renderer as unknown as { _renderContexts: RenderContexts | null })._renderContexts;
  if (!contexts) throw new Error('Streaming shader preparation requires an initialized renderer');
  let budget = budgets.get(renderer);
  if (!budget) { budget = new PreparationBudget(); budgets.set(renderer, budget); }
  const pending = budget.checkpoint();
  if (pending) await waitFor(pending);
  model.updateWorldMatrix(true, true);
  const meshes: THREE.Mesh[] = [];
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh && (mesh.visible || mesh.userData.vegetationLod || mesh.userData.prepareHidden) && mesh.layers.test(camera.layers)
      && (!castersOnly || mesh.castShadow)) meshes.push(mesh);
  });
  const programs = new Set<string>();
  for (const mesh of meshes) {
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material.visible || (opaqueOnly && material.transparent)) continue;
      const signature = programSignature(mesh, material);
      if (signature !== null) { if (programs.has(signature)) continue; programs.add(signature); }
      const oldTarget = renderer.getRenderTarget(), oldMrt = renderer.getMRT(), oldContext = renderer.contextNode;
      const oldMaterial = mesh.material, children = mesh.children, visible = mesh.visible, culled = mesh.frustumCulled;
      const previousOverride = scene.overrideMaterial;
      const onBuilder = (builder: { renderer: unknown }, value: unknown) => {
        const draw = value as { object: THREE.Object3D; material: THREE.Material; camera: THREE.Camera; context: unknown };
        if (draw.object === mesh && (draw.material === material || override !== null) && draw.camera === camera) {
          built = true;
          // NodeBuilder.buildAsync reads renderer state again after scheduler.yield(). A renderer view
          // keeps those reads in this pass while the live renderer continues the game's animation.
          builder.renderer = new Proxy(renderer, { get(source, key, receiver) {
            if (key === '_renderTarget') return target;
            if (key === '_mrt') return mrt;
            if (key === 'contextNode') return context;
            if (key === '_currentRenderContext') return draw.context;
            return Reflect.get(source, key, receiver);
          } });
        }
      };
      let built = false;
      const removeHook = addNodeBuilderHook(renderer, onBuilder as (builder: unknown, value: unknown) => void);
      // r186 compileAsync selects depth zero. Nested post/reflection draws use other contexts,
      // whose IDs are part of shader keys; use the depth observed by the actual pass.
      const getContext = contexts.get;
      // compileAsync reads the target scene's lights synchronously; serve this frame's walk instead of a new one.
      const lights = visibleLights(renderer, scene), walk = Object.getOwnPropertyDescriptor(scene, 'traverseVisible');
      let compiled: Promise<void>, started = 0;
      try {
        scene.traverseVisible = callback => { for (const light of lights) callback(light); };
        contexts.get = function (target, mrt, depth = passDepth) { return getContext.call(this, target, mrt, depth); };
        renderer.setRenderTarget(target); renderer.setMRT(mrt); renderer.contextNode = context;
        scene.overrideMaterial = override;
        mesh.material = material; mesh.children = []; mesh.visible = true; mesh.frustumCulled = false;
        started = performance.now();
        compileStats.calls++;
        compiled = renderer.compileAsync(mesh, camera, scene);
        streamTrace.slice('compile', performance.now() - started);
        void compiled.then(() => { compileStats.count++; compileStats.ms += performance.now() - started; }, () => {});
      } catch (error) {
        removeHook();
        throw error;
      } finally {
        if (walk) Object.defineProperty(scene, 'traverseVisible', walk);
        else delete (scene as { traverseVisible?: unknown }).traverseVisible;
        contexts.get = getContext;
        scene.overrideMaterial = previousOverride;
        renderer.setRenderTarget(oldTarget); renderer.setMRT(oldMrt); renderer.contextNode = oldContext;
        mesh.material = oldMaterial; mesh.children = children; mesh.visible = visible; mesh.frustumCulled = culled;
      }
      try { await compiled; }
      finally {
        removeHook();
        const ms = performance.now() - started;
        if (built) { compileStats.builds++; compileStats.buildMs += ms; } else compileStats.hitMs += ms;
      }
      const nextFrame = budget.checkpoint();
      if (nextFrame) await waitFor(nextFrame);
    }
  }
}
