import { fleetAppearance } from '../../src/actors/fleet/catalog';
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { mrt, normalView, velocity } from 'three/tsl';
// @ts-expect-error r186 internal pipeline manager has no DefinitelyTyped declaration.
import Pipelines from 'three/src/renderers/common/Pipelines.js';
import { ParkedCarVisuals } from '../../src/actors/car';
import { actorPaintOf, instancePaintOf, paintOf } from '../../src/actors/parkedPaint';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { draftManifest, type ParkedVehicle } from '../../src/world/manifest';
import type { VehicleState } from '../../src/physics/protocol';

type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
const car = (object_id: string, model: string, color: string, x=0): ParkedVehicle => ({ object_id,model,color,x,y:2,z:5,yaw:0,width:1.85,length:4.55,height:1.48,clear_front_m:10,clear_rear_m:10 });

test('resting, claimed, driven and remote fleet cars preserve pose and paint',async()=>{
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=[car('a','fleet-sedan-0','red',10),car('b','fleet-sedan-0','blue',20),car('c','fleet-sedan-0','white',30)];await visuals.load(manifest);
  const rest={restingPose:(c:ParkedVehicle)=>c.object_id==='c'?null:{pos:{x:c.x,y:c.y+.75,z:c.z},rot:{x:0,y:0,z:0,w:1}}};
  visuals.update([{id:'a',pos:{x:11,y:3,z:5},rot:{x:0,y:0,z:0,w:1}} as VehicleState],rest);
  const draw=scene.getObjectByName(`parked/${fleetAppearance('fleet-sedan-0','a',manifest.vehicles[0]).id}/0`) as THREE.InstancedMesh;
  expect(draw.count).toBe(2);const matrix=new THREE.Matrix4();draw.getMatrixAt(0,matrix);
  expect(new THREE.Vector3().setFromMatrixPosition(matrix).toArray()).toEqual([11,2.25,5]);
  draw.getMatrixAt(1,matrix);expect(new THREE.Vector3().setFromMatrixPosition(matrix).toArray()).toEqual([20,2,5]);
  expect(Array.from(instancePaintOf(draw)!.array.slice(0,3))).toEqual(paintOf('red').map(Math.fround));
  visuals.takeOver('a');visuals.update([],rest);
  const driven=scene.getObjectByName('driven/a') as THREE.Mesh;
  expect(driven.material).toBe(draw.material);expect(driven.geometry).toBe(draw.geometry);
  expect(actorPaintOf(driven)!.toArray()).toEqual([...paintOf('red')]);expect(draw.count).toBe(1);
  const remote=visuals.remoteModel('fleet-sedan-0/beige')!;
  expect(remote.material).toBe(draw.material);expect(remote.geometry).toBe(draw.geometry);
  expect(actorPaintOf(remote)!.toArray()).toEqual([...paintOf('beige')]);
});

test.each([false,true])('all fleet models, LODs and capacities share one instanced program (velocity=%s)',async motion=>{
  const renderer=new GameRenderer({canvas:{width:1,height:1,style:{}} as HTMLCanvasElement});
  renderer.hasFeature=()=>false;Object.assign(renderer.backend,{capabilities:{getUniformBufferLimit:()=>65536}});
  if(motion){renderer.setRenderTarget(new THREE.RenderTarget());renderer.setMRT(mrt({output:normalView,velocity}));}
  const backend=renderer.backend as typeof renderer.backend & {createNodeBuilder(object:THREE.Object3D,renderer:THREE.WebGPURenderer):ShaderBuilder};
  const program=(object:THREE.Mesh)=>{
    const builder=backend.createNodeBuilder(object,renderer);renderer.debug.onNodeBuilderCreated!(builder,{object} as never);
    builder.scene=new THREE.Scene();builder.camera=new THREE.PerspectiveCamera();builder.build();
    const generated=(builder as unknown as {bufferAttributes:{node:{attribute:unknown}}[]}).bufferAttributes.map(a=>a.node.attribute);
    return {vertexShader:builder.vertexShader,fragmentShader:builder.fragmentShader,generated};
  };
  const scene=new THREE.Scene(),visuals=new ParkedCarVisuals(scene),manifest=draftManifest('http://localhost/');
  manifest.vehicles=['fleet-sedan-0','fleet-estate-3','fleet-van-6'].flatMap((m,k)=>Array.from({length:2**(k+2)},(_,i)=>car(`${m}/${i}`,m,i%2?'silver':'blue')));
  await visuals.load(manifest);
  const meshes=scene.children as THREE.InstancedMesh[],batched=meshes.map(program);
  const singles=['fleet-hatch-0','fleet-van-6'].map(m=>program(visuals.remoteModel(`${m}/red`)!));
  const variants=(states:{vertexShader:string;fragmentShader:string}[])=>new Set(states.map(s=>s.vertexShader+s.fragmentShader)).size;
  expect(variants(batched)).toBe(1);expect(variants(singles)).toBe(1);
  meshes.forEach((mesh,i)=>expect(batched[i].generated).toContain(instancePaintOf(mesh)));
  expect(batched[0].fragmentShader).toContain('sRGBTransferEOTF');
  expect(batched[0].vertexShader).not.toMatch(/array<mat4x4<f32>,\s*\d+>/);
  const pipelineBackend={createProgram:vi.fn(),createRenderPipeline:vi.fn(),needsRenderUpdate:()=>false,getRenderCacheKey:()=> 'pass'};
  const pipelines=new Pipelines(pipelineBackend,{}, {createProgram:vi.fn(),destroyProgram:vi.fn()});
  for(const state of [...batched,...singles])pipelines.getForRender({material:{name:'fleet'},getNodeBuilderState:()=>state});
  expect(pipelineBackend.createRenderPipeline).toHaveBeenCalledTimes(2);pipelines.dispose();
});
