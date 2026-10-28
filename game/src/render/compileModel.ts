import type * as THREE from 'three/webgpu';
import { PreparationBudget } from './preparationBudget';

const budgets = new WeakMap<THREE.WebGPURenderer, PreparationBudget>();

/** Compile one draw at a time so r186 captures its pass state before yielding, then restore it immediately. */
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
      let compiled: Promise<void>;
      try {
        renderer.setRenderTarget(target); renderer.setMRT(mrt); renderer.contextNode = context;
        mesh.material = material; mesh.children = []; mesh.visible = true; mesh.frustumCulled = false;
        compiled = renderer.compileAsync(mesh, camera, scene);
      } finally {
        renderer.setRenderTarget(oldTarget); renderer.setMRT(oldMrt); renderer.contextNode = oldContext;
        mesh.material = oldMaterial; mesh.children = children; mesh.visible = visible; mesh.frustumCulled = culled;
      }
      await compiled;
      const nextFrame = budget.checkpoint();
      if (nextFrame) await nextFrame;
    }
  }
}
