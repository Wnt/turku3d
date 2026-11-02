/** Runtime-only inspection hook for the fixed street camera. No extra startup URL parameters.
 * await import('/src/actors/fleet/qa.ts').then(m => m.installFleetQA()) */
import * as THREE from 'three/webgpu';
import { ParkedCarVisuals } from './visuals';
import { fleetChassis } from './pose';
import { slotRotation } from '../../world/parkedVehicles';
import { slotShown } from '../../world/parkedOccupancy';
import type { Manifest, ParkedVehicle } from '../../world/manifest';

export async function installFleetQA(Visuals=ParkedCarVisuals) {
  const game=window.__game as Record<string,any>;
  if(!game?.lights?.source?.host)throw new Error('Load a fixed street camera first');
  game.fleetQA?.dispose();
  const host=game.lights.source.host, manifest=host.manifest as Manifest;
  const root=new THREE.Group();root.name='fleet-qa';host.scene.add(root);
  const visuals=new Visuals(root);
  const rest={restingPose:(car:ParkedVehicle)=>slotShown(car,game.dayClock.night)?{pos:fleetChassis(car,slotRotation(car)),rot:slotRotation(car)}:null};
  await visuals.load(manifest);
  const previous=window.__render!.afterFrame;
  const tick=()=>{previous?.();visuals.setView?.(window.__render!.camera,game.dayClock.night);visuals.update([],rest);};
  window.__render!.afterFrame=tick;tick();
  const api={visuals,root,manifest,
    async refresh(){await visuals.load(manifest);tick();return visuals.snapshot?.();},
    dispose(){visuals.dispose?.();root.removeFromParent();if(window.__render!.afterFrame===tick)window.__render!.afterFrame=previous;},
    snapshot:()=>visuals.snapshot?.(),
  };
  game.fleetQA=api;return {records:manifest.vehicles?.length,...visuals.snapshot?.()};
}
if(import.meta.hot)import.meta.hot.accept();
