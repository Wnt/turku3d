import type * as THREE from 'three/webgpu';

/** Include the tileset transform during preparation, then leave insertion to the loader's visibility lifecycle. */
export async function prepareDetached(model: THREE.Object3D, parent: THREE.Object3D, prepare: () => Promise<void>) {
  // TilesGroup caches an inverse in updateMatrixWorld; inherited updateWorldMatrix alone bypasses that cache.
  parent.updateMatrixWorld(true);
  const previous = model.parent;
  model.parent = parent;
  try {
    model.updateWorldMatrix(true, true);
    await prepare();
  } finally { model.parent = previous; }
}
