import * as T from 'three/webgpu';
import { racerModel } from './model';

/** Four independently steered/spun wheels plus the spare share four near draws and one distant draw. */
export function createRaceWheels(){
  const lod=new T.LOD();lod.name='korpi-wheel-batches';
  for(let level=0;level<3;level++){
    const group=new T.Group();group.name=`korpi-wheels-${level}`;
    racerModel(level).wheel.traverse(object=>{
      const source=object as T.Mesh;if(!source.isMesh)return;
      const mesh=new T.InstancedMesh(source.geometry,source.material,5);
      mesh.name=`korpi-wheel-batch-${level}/${(source.material as T.Material).name}`;
      mesh.boundingBox=new T.Box3(new T.Vector3(-2.3,-1.8,-1.6),new T.Vector3(2.3,.9,1.6));
      mesh.boundingSphere=new T.Sphere(new T.Vector3(0,-.3,0),3.4);
      mesh.castShadow=true;mesh.receiveShadow=true;group.add(mesh);
    });
    lod.addLevel(group,[0,35,90][level],.1);
  }
  return lod;
}
