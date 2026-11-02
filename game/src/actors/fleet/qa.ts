/** Runtime-only inspection hook for the fixed street camera. No extra startup URL parameters.
 * await import('/src/actors/fleet/qa.ts').then(m => m.installFleetQA()) */
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from './visuals';
import { slotRotation } from '../../world/parkedVehicles';
import { slotShown } from '../../world/parkedOccupancy';
import type { Manifest, ParkedVehicle } from '../../world/manifest';
import {FLEET_BY_ID} from './catalog';
import {buildFleetGeometry} from './geometry';

export async function installFleetQA(Visuals=ParkedCarVisuals) {
  const game=window.__game as Record<string,any>;
  if(!game?.lights?.source?.host)throw new Error('Load a fixed street camera first');
  game.fleetQA?.dispose();
  const host=game.lights.source.host, manifest=host.manifest as Manifest;
  const root=new THREE.Group();root.name='fleet-qa';host.scene.add(root);
  const visuals=new Visuals(root);
  const rest={restingPose:(car:ParkedVehicle)=>slotShown(car,game.dayClock.night)?{pos:{x:car.x,y:car.y+.75,z:car.z},rot:slotRotation(car)}:null};
  await visuals.load(manifest);
  const previous=window.__render!.afterFrame;
  const tick=()=>{previous?.();visuals.setView?.(window.__render!.camera,game.dayClock.night);visuals.update([],rest);};
  window.__render!.afterFrame=tick;tick();
  const api={visuals,root,manifest,
    async bench(){
      const render=window.__render!,before=render.beforeFrame,after=render.afterFrame;
      // Native slots only, with per-car frustum culling disabled for a conservative
      // 100+ car stress load. Instanced-mesh GPU culling and distance LOD remain active.
      (visuals as unknown as {hasView:boolean}).hasView=false;
      visuals.update([],rest);
      const fleet=visuals.snapshot();
      if(fleet.instances<100){visuals.setView(render.camera,game.dayClock.night);throw Error(`Residential stress load has only ${fleet.instances} native cars`);}
      const bodies=root.children.filter(o=>o.name.startsWith('parked/')&&(o as THREE.InstancedMesh).count>0) as THREE.InstancedMesh[];
      const wheels=root.children.filter(o=>o.name.startsWith('wheels/'));
      const original=bodies.map(b=>b.geometry),visible=wheels.map(w=>w.visible);
      const baseline=bodies.map(b=>{const [,id,lod]=b.name.split('/');return id==='fleet-estate-3'?b.geometry:buildFleetGeometry(FLEET_BY_ID.get(id)!,Number(lod) as 0|1|2);});
      const results=[];
      try{
        render.beforeFrame=undefined;
        for(const articulated of [false,true]){
          bodies.forEach((b,i)=>b.geometry=articulated?original[i]:baseline[i]);
          wheels.forEach((w,i)=>w.visible=articulated&&visible[i]);
          const intervals:number[]=[],draws:number[]=[];let frames=0,previous=performance.now();
          await new Promise<void>(resolve=>{
            render.afterFrame=()=>{
              const now=performance.now();
              if(frames++>=60){intervals.push(now-previous);draws.push(render.renderer.info.render.drawCalls);}
              previous=now;if(intervals.length===180)resolve();
            };
          });
          if(visuals.snapshot().instances!==fleet.instances)throw Error('Fleet changed during benchmark');
          results.push({articulated,fps:1000/(intervals.reduce((a,b)=>a+b,0)/intervals.length),draws:Math.max(...draws),instances:fleet.instances});
        }
      }finally{
        bodies.forEach((b,i)=>{b.geometry=original[i];if(baseline[i]!==original[i])baseline[i].dispose();});
        wheels.forEach((w,i)=>w.visible=visible[i]);render.beforeFrame=before;render.afterFrame=after;
        visuals.setView(render.camera,game.dayClock.night);
      }
      return {benchRevision:2,results,fleet,perCarFrustumCulling:false,source:'native resident parking slots',pose:window.__qaPose};
    },
    async refresh(){await visuals.load(manifest);tick();return visuals.snapshot?.();},
    dispose(){visuals.dispose?.();root.removeFromParent();if(window.__render!.afterFrame===tick)window.__render!.afterFrame=previous;},
    snapshot:()=>visuals.snapshot?.(),
  };
  game.fleetQA=api;return {records:manifest.vehicles?.length,...visuals.snapshot?.()};
}
if(import.meta.hot)import.meta.hot.accept();
