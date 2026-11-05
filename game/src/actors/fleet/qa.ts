/** Runtime-only inspection hook for the fixed street camera. No extra startup URL parameters.
 * await import('/src/actors/fleet/qa.ts').then(m => m.installFleetQA()) */
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from './visuals';
import { slotRotation } from '../../world/parkedVehicles';
import { slotShown } from '../../world/parkedOccupancy';
import type { Manifest, ParkedVehicle } from '../../world/manifest';

export async function installFleetQA(Visuals=ParkedCarVisuals, manifestOverride?: Manifest) {
  const game=window.__game as Record<string,any>;
  if(!game?.lights?.source?.host)throw new Error('Load a fixed street camera first');
  game.fleetQA?.dispose();
  const host=game.lights.source.host, manifest=(manifestOverride ?? host.manifest) as Manifest;
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
      const intervals:number[]=[],draws:number[]=[];
      try{
        render.beforeFrame=undefined;
        let frames=0,previous=performance.now();
        await new Promise<void>(resolve=>{
          render.afterFrame=()=>{
            const now=performance.now();
            if(frames++>=60){intervals.push(now-previous);draws.push(render.renderer.info.render.drawCalls);}
            previous=now;if(intervals.length===180)resolve();
          };
        });
        if(visuals.snapshot().instances!==fleet.instances)throw Error('Fleet changed during benchmark');
      }finally{render.beforeFrame=before;render.afterFrame=after;visuals.setView(render.camera,game.dayClock.night);}
      const results=[{fps:1000/(intervals.reduce((a,b)=>a+b,0)/intervals.length),draws:Math.max(...draws),instances:fleet.instances}];
      return {benchRevision:3,results,fleet,perCarFrustumCulling:false,source:'native resident parking slots',pose:window.__qaPose};
    },
    async refresh(){await visuals.load(manifest);tick();return visuals.snapshot?.();},
    dispose(){visuals.dispose?.();root.removeFromParent();if(window.__render!.afterFrame===tick)window.__render!.afterFrame=previous;},
    snapshot:()=>visuals.snapshot?.(),
  };
  game.fleetQA=api;return {records:manifest.vehicles?.length,...visuals.snapshot?.()};
}
if(import.meta.hot)import.meta.hot.accept();
