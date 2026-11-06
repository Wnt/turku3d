import * as THREE from 'three/webgpu';
import type { WheelState } from '../../physics/protocol';
import { CHASSIS_LIFT } from '../../traffic/constants';
import { paintActor } from '../parkedPaint';
import type { FleetModel } from './catalog';

/** Fleet meshes face +Z; physics faces +X. Hubs remain in the model's wheel arches. */
export function applyFleetWheels(actor: THREE.Object3D, wheels: readonly WheelState[]) {
  const model = actor.userData.fleetModel as FleetModel | undefined;
  if (!model) return;
  wheels.forEach((state, i) => {
    const hub = actor.getObjectByName(`fleet-wheel-${i}`);
    if (!hub) return;
    // A newly sleeping Rapier controller has no ray result yet (zero length, no contact).
    // Keep the authored rest pivot until an actual suspension result arrives.
    if(state.contact||state.suspension!==0)hub.position.y = (state.pos.y + CHASSIS_LIFT) / actor.scale.y;
    hub.rotation.y = state.steer;
    hub.children[0].rotation.x = state.rotation;
  });
}

/** Authored wheels preserve their hub pivots and side-specific rim geometry. */
export function addAuthoredFleetWheels(actor:THREE.Object3D,model:FleetModel,
  wheels:readonly import('./authoredWheelParts').AuthoredWheelPart[],material:THREE.Material) {
  actor.userData.fleetModel=model;
  wheels.forEach((part,i)=>{
    const hub=new THREE.Group();hub.name=`fleet-wheel-${i}`;
    hub.position.set(part.pivot.x,part.pivot.y,part.pivot.z);hub.userData.pivot={...part.pivot};
    const mesh=new THREE.Mesh(part.geometry,material);paintActor(mesh,'silver');
    mesh.castShadow=true;mesh.receiveShadow=true;hub.add(mesh);actor.add(hub);
  });
}
