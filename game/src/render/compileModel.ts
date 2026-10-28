import type * as THREE from 'three/webgpu';
import { PreparationBudget } from './preparationBudget';

const budgets = new WeakMap<THREE.WebGPURenderer, PreparationBudget>();

/** Compile hidden draws with stable pass state across r186's asynchronous shader stages. */
export async function compileModel(renderer: THREE.WebGPURenderer, model: THREE.Object3D, scene: THREE.Scene,
  camera: THREE.PerspectiveCamera, target: THREE.RenderTarget, mrt: ReturnType<THREE.WebGPURenderer['getMRT']> | null,
  context: THREE.WebGPURenderer['contextNode'], opaqueOnly = false) {
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
      if (!material.visible || material.name === 'river-planar' || (opaqueOnly && material.transparent)) continue;
      const oldTarget = renderer.getRenderTarget(), oldMrt = renderer.getMRT(), oldContext = renderer.contextNode;
      const oldMaterial = mesh.material, children = mesh.children, visible = mesh.visible, culled = mesh.frustumCulled;
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
      let compiled: Promise<void>;
      try {
        renderer.setRenderTarget(target); renderer.setMRT(mrt); renderer.contextNode = context;
        mesh.material = material; mesh.children = []; mesh.visible = true; mesh.frustumCulled = false;
        compiled = renderer.compileAsync(mesh, camera, scene);
      } catch (error) {
        renderer.debug.onNodeBuilderCreated = previousBuilder;
        throw error;
      } finally {
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
