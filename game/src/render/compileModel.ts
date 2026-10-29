import type * as THREE from 'three/webgpu';
import { PreparationBudget } from './preparationBudget';

type RenderContexts = { get(target: THREE.RenderTarget | null, mrt: ReturnType<THREE.WebGPURenderer['getMRT']>, depth?: number): unknown };

/** r186 increments this before rendering a scene. Capture a nested pass before its render() call. */
export const nextRenderDepth = (renderer: THREE.WebGPURenderer) =>
  (renderer as unknown as { _callDepth: number })._callDepth + 1;

/** Image-producing nodes must not redraw the resident world from compileAsync's update callbacks. */
export const isPrecompiling = (renderer: THREE.WebGPURenderer) =>
  (renderer as unknown as { _isPreCompiling: boolean })._isPreCompiling === true;

const budgets = new WeakMap<THREE.WebGPURenderer, PreparationBudget>();

/** Compile hidden draws with stable pass state across r186's asynchronous shader stages. */
export async function compileModel(renderer: THREE.WebGPURenderer, model: THREE.Object3D, scene: THREE.Scene,
  camera: THREE.PerspectiveCamera, target: THREE.RenderTarget, mrt: ReturnType<THREE.WebGPURenderer['getMRT']> | null,
  context: THREE.WebGPURenderer['contextNode'], opaqueOnly = false, passDepth = 0, override: THREE.Material | null = null) {
  const contexts = (renderer as unknown as { _renderContexts: RenderContexts | null })._renderContexts;
  if (!contexts) throw new Error('Streaming shader preparation requires an initialized renderer');
  let budget = budgets.get(renderer);
  if (!budget) { budget = new PreparationBudget(); budgets.set(renderer, budget); }
  const pending = budget.checkpoint();
  if (pending) await pending;
  model.updateWorldMatrix(true, true);
  const meshes: THREE.Mesh[] = [];
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh && (mesh.visible || mesh.userData.vegetationLod) && mesh.layers.test(camera.layers)) meshes.push(mesh);
  });
  for (const mesh of meshes) {
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material.visible || (opaqueOnly && material.transparent)) continue;
      const oldTarget = renderer.getRenderTarget(), oldMrt = renderer.getMRT(), oldContext = renderer.contextNode;
      const oldMaterial = mesh.material, children = mesh.children, visible = mesh.visible, culled = mesh.frustumCulled;
      const previousOverride = scene.overrideMaterial;
      const previousBuilder = renderer.debug.onNodeBuilderCreated;
      const onBuilder: typeof previousBuilder = (builder, value) => {
        const draw = value as { object: THREE.Object3D; material: THREE.Material; camera: THREE.Camera; context: unknown };
        if (draw.object === mesh && draw.material === material && draw.camera === camera) {
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
        previousBuilder?.(builder, value);
      };
      renderer.debug.onNodeBuilderCreated = onBuilder;
      // r186 compileAsync selects depth zero. Nested post/reflection draws use other contexts,
      // whose IDs are part of shader keys; use the depth observed by the actual pass.
      const getContext = contexts.get;
      let compiled: Promise<void>;
      try {
        contexts.get = function (target, mrt, depth = passDepth) { return getContext.call(this, target, mrt, depth); };
        renderer.setRenderTarget(target); renderer.setMRT(mrt); renderer.contextNode = context;
        scene.overrideMaterial = override;
        mesh.material = material; mesh.children = []; mesh.visible = true; mesh.frustumCulled = false;
        compiled = renderer.compileAsync(mesh, camera, scene);
      } catch (error) {
        renderer.debug.onNodeBuilderCreated = previousBuilder;
        throw error;
      } finally {
        contexts.get = getContext;
        scene.overrideMaterial = previousOverride;
        renderer.setRenderTarget(oldTarget); renderer.setMRT(oldMrt); renderer.contextNode = oldContext;
        mesh.material = oldMaterial; mesh.children = children; mesh.visible = visible; mesh.frustumCulled = culled;
      }
      try { await compiled; }
      finally { if (renderer.debug.onNodeBuilderCreated === onBuilder) renderer.debug.onNodeBuilderCreated = previousBuilder; }
      const nextFrame = budget.checkpoint();
      if (nextFrame) await nextFrame;
    }
  }
}
