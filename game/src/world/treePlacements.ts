import * as THREE from 'three/webgpu';
import { attachStatic } from './frozenTransforms';

export interface TreePlacements {
  version: 1;
  nodes: { variant:string; matrix:number[]; positions:number[][]; scales:number[][]; rotations:number[][];
    ids:string[]; species:string[]; nearWater?:boolean[] }[];
}

/** Both first-pass sidecars and authored full GLBs feed the same instanced vegetation renderer. */
export function addTreePlacements(model: THREE.Object3D, data: TreePlacements) {
  if (model.userData.treePlacements || data.version !== 1) return;
  model.userData.treePlacements = true;
  model.updateWorldMatrix(true, false);
  const inverse = model.matrixWorld.clone().invert();
  for (const node of data.nodes) {
    if (!node.positions.length) continue;
    const root = new THREE.Group();
    root.userData = { layer:'vegetation', treePlacements:true };
    root.matrixAutoUpdate = false;
    root.matrix.multiplyMatrices(inverse,new THREE.Matrix4().fromArray(node.matrix));
    // This tiny source carries transforms only; VegetationLod hides it before publishing the model.
    const source = new THREE.InstancedMesh(new THREE.BoxGeometry(2,1,2),new THREE.MeshStandardMaterial(),node.positions.length);
    source.visible = false;
    source.userData = { layer:'vegetation',variant:node.variant,instance_object_ids:node.ids,
      instance_species:node.species,instance_near_water:node.nearWater };
    const p=new THREE.Vector3(),q=new THREE.Quaternion(),s=new THREE.Vector3(),m=new THREE.Matrix4();
    for(let i=0;i<source.count;i++) source.setMatrixAt(i,m.compose(p.fromArray(node.positions[i]),q.fromArray(node.rotations[i]),s.fromArray(node.scales[i])));
    root.add(source); attachStatic(model, root);
  }
}
