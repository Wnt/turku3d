import { beforeAll, expect, test, vi } from 'vitest';
import * as T from 'three/webgpu';
import { TRUCK } from '../../src/physics/constants';
import { racerModel } from '../../src/actors/prerunner/model';
import { TruckVisual } from '../../src/actors/truck';
import { applyRaceWheels } from '../../src/actors/prerunner/rig';
import { shockTop } from '../../src/actors/prerunner/hardpoints';

beforeAll(() => {
  const context = { fillRect() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, fill() {}, fillText() {} };
  vi.stubGlobal('document', { createElement: () => ({ getContext: () => context }) });
});
function budget(root: T.Object3D) {
  let triangles = 0, draws = 0;
  root.traverse(object => {
    const mesh = object as T.Mesh;
    if (mesh.isMesh) { triangles += (mesh.geometry.index?.count ?? mesh.geometry.attributes.position.count) / 3; draws++; }
  });
  return { triangles, draws };
}
test('visible tread stays inside the authoritative contact radius', () => {
  for (const lod of [0, 1, 2]) racerModel(lod).wheel.traverse(object => {
    const mesh = object as T.Mesh;
    if (!mesh.isMesh) return;
    const positions = mesh.geometry.attributes.position;
    for (let i = 0; i < positions.count; i++) expect(Math.hypot(positions.getX(i), positions.getY(i))).toBeLessThanOrEqual(TRUCK.wheelRadius + .005);
  });
});
test('race meshes share geometry and reduce the distance triangle budget', () => {
  const costs = [0, 1, 2].map(lod => {
    const model = racerModel(lod), body = budget(model.body), wheel = budget(model.wheel);
    return { triangles: body.triangles + 4 * wheel.triangles, draws: body.draws + 4 * wheel.draws };
  });
  expect(costs[0].triangles).toBeLessThan(35000);
  expect(costs[2].triangles).toBeLessThan(costs[0].triangles * .25);
  expect(costs[2].draws).toBeLessThanOrEqual(6);
  expect(racerModel(0)).toBe(racerModel(0));
  console.log(JSON.stringify({ raceMeshBudgets: costs }));
});
test('remote clone articulates without changing local wheel or suspension matrices', async () => {
  const local = new TruckVisual(new T.Group()); await local.load();
  const remote = local.root.clone(true);
  const localLinks = local.root.getObjectByName('korpi-links') as T.InstancedMesh;
  const remoteLinks = remote.getObjectByName('korpi-links') as T.InstancedMesh;
  const before = Array.from(localLinks.instanceMatrix.array);
  const wheels = TRUCK.wheels.map((p, i) => ({ pos: { x: p.x, y: p.y - TRUCK.rest + (i % 2 ? .2 : -.2), z: p.z }, steer: i < 2 ? .4 : 0, rotation: 1.3 }));
  applyRaceWheels(remote, wheels);
  expect(Array.from(localLinks.instanceMatrix.array)).toEqual(before);
  expect(Array.from(remoteLinks.instanceMatrix.array)).not.toEqual(before);
  wheels.forEach((wheel, i) => {
    const pivot = remote.getObjectByName(`vehicle-wheel-${i}`)!;
    expect(pivot.position.toArray()).toEqual([wheel.pos.x, wheel.pos.y, wheel.pos.z]);
    expect(pivot.rotation.y).toBeCloseTo(wheel.steer);
  });
  local.dispose();
});


test('damper bodies attach to the shared hardpoint and uprights retain their length through full travel', async () => {
  const actor=new TruckVisual(new T.Group());await actor.load();
  const dampers=actor.root.getObjectByName('korpi-dampers') as T.InstancedMesh;
  const links=actor.root.getObjectByName('korpi-links') as T.InstancedMesh;
  const matrix=new T.Matrix4(),point=new T.Vector3(),expected=new T.Vector3();
  for(const travel of [-TRUCK.suspensionTravel,0,TRUCK.suspensionTravel]){
    const wheels=TRUCK.wheels.map(w=>({pos:{x:w.x,y:w.y-TRUCK.rest+travel,z:w.z},steer:.45,rotation:0}));
    applyRaceWheels(actor.root,wheels);
    for(let i=0;i<4;i++){
      dampers.getMatrixAt(i*15,matrix);point.set(0,-.5,0).applyMatrix4(matrix);shockTop(TRUCK.wheels[i],expected);
      expect(point.distanceTo(expected)).toBeLessThan(.00001);
      links.getMatrixAt(i*6+4,matrix);
      const a=new T.Vector3(0,-.5,0).applyMatrix4(matrix),b=new T.Vector3(0,.5,0).applyMatrix4(matrix);
      expect(a.distanceTo(b)).toBeCloseTo(.34,5);
    }
  }
  actor.dispose();
});

test('the complete near actor batches its four wheels and spare without redundant distant geometry draws', async () => {
  const actor=new TruckVisual(new T.Group());await actor.load();
  const count=(object:T.Object3D,level:number):{triangles:number;draws:number}=>{
    if((object as T.LOD).isLOD)return count((object as T.LOD).levels[level===0?0:Math.min(level,(object as T.LOD).levels.length-1)].object,level);
    const mesh=object as T.Mesh;
    const own=mesh.isMesh?{triangles:(mesh.geometry.index?.count??mesh.geometry.attributes.position.count)/3*((mesh as T.InstancedMesh).isInstancedMesh?(mesh as T.InstancedMesh).count:1),draws:1}:{triangles:0,draws:0};
    for(const child of object.children){const cost=count(child,level);own.triangles+=cost.triangles;own.draws+=cost.draws;}
    return own;
  };
  const near=count(actor.root,0),far=count(actor.root,2);
  expect(near.draws).toBeLessThanOrEqual(26);expect(near.triangles).toBeLessThan(55000);
  expect(far.draws).toBe(2);expect(far.triangles).toBeLessThan(6000);
  console.log(JSON.stringify({completeRaceBudget:{near,far}}));actor.dispose();
});
