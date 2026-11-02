import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { addTreePlacements, type TreePlacements } from '../../src/world/treePlacements';
import { VegetationLod } from '../../src/world/vegetationLod';
import { natureFixture } from './natureFixture';

test('first placements and full instancing produce identical species, transforms and per-tree LOD', () => {
  const library=natureFixture(), scene=new THREE.Scene();
  const matrix=new THREE.Matrix4().makeTranslation(100,0,0);
  const data:TreePlacements={version:1,nodes:[{variant:'tree_default_fall',matrix:matrix.toArray(),
    positions:[[0,0,-10],[0,0,-80],[0,0,-200]],scales:[[4,15,4],[4,15,4],[4,15,4]],
    rotations:Array.from({length:3},()=>[0,0,0,1]),ids:['register:1','register:2','register:3'],
    species:['Tilia cordata','Betula pendula','Picea abies'],nearWater:[false,false,false]}]};
  const first=new THREE.Group(),full=new THREE.Group(),root=new THREE.Group();
  root.position.x=100;full.add(root);
  const source=new THREE.InstancedMesh(new THREE.BoxGeometry(),new THREE.MeshStandardMaterial(),3);
  source.userData={layer:'vegetation',variant:data.nodes[0].variant,instance_object_ids:data.nodes[0].ids,instance_species:data.nodes[0].species};
  const m=new THREE.Matrix4(),q=new THREE.Quaternion(),p=new THREE.Vector3(),s=new THREE.Vector3();
  for(let i=0;i<3;i++)source.setMatrixAt(i,m.compose(p.fromArray(data.nodes[0].positions[i]),q,s.set(4,15,4)));
  root.add(source);scene.add(first,full);
  addTreePlacements(first,data);addTreePlacements(first,data);
  expect(first.children).toHaveLength(1);
  const lod=new VegetationLod('medium',library),camera=new THREE.PerspectiveCamera(65,1,.1,1000);camera.position.x=100;
  const signature=(model:THREE.Object3D)=>{
    const rows:unknown[]=[];
    model.traverse(object=>{const mesh=object as THREE.InstancedMesh;
      if(!mesh.isInstancedMesh||!mesh.visible||!mesh.userData.vegetationLod||mesh.userData.shadowProxy||mesh.material===library.materials.bark)return;
      rows.push({asset:mesh.userData.nature,level:mesh.userData.level,ids:mesh.userData.instance_object_ids,
        matrices:Array.from(mesh.instanceMatrix.array).slice(0,mesh.count*16),world:mesh.matrixWorld.toArray()});});
    return rows;
  };
  lod.add(first);full.visible=false;lod.update(camera);const a=signature(first);
  first.visible=false;full.visible=true;lod.add(full);lod.update(camera);const b=signature(full);
  expect(a).toEqual(b);expect(a).toHaveLength(3);
  lod.remove(first);first.removeFromParent();
  expect(signature(full)).toEqual(b);lod.dispose();
});
