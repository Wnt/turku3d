import * as THREE from 'three/webgpu';
import type { WheelState } from '../../physics/protocol';
import { CHASSIS_LIFT } from '../../traffic/constants';
import type { FleetModel } from './catalog';

/** Fleet meshes face +Z; physics faces +X. Hubs remain in the model's wheel arches. */
export function applyFleetWheels(actor: THREE.Object3D, wheels: readonly WheelState[]) {
  const model = actor.userData.fleetModel as FleetModel | undefined;
  if (!model) return;
  wheels.forEach((state, i) => {
    const hub = actor.getObjectByName(`fleet-wheel-${i}`);
    if (!hub) return;
    hub.position.y = (state.pos.y + CHASSIS_LIFT) / actor.scale.y;
    hub.rotation.y = state.steer;
    hub.children[0].rotation.x = state.rotation;
  });
}

export function addFleetWheels(actor: THREE.Object3D, model: FleetModel,
  geometry: (side: number) => THREE.BufferGeometry, material: THREE.Material) {
  actor.userData.fleetModel = model;
  for (let i = 0; i < 4; i++) {
    const side = i % 2 ? -1 : 1;
    const hub = new THREE.Group(); hub.name = `fleet-wheel-${i}`;
    hub.position.set(side * (model.width / 2 - .075), model.wheel, (i < 2 ? 1 : -1) * model.wheelbase / 2);
    const wheel = new THREE.Mesh(geometry(side), material);
    wheel.castShadow = true; wheel.receiveShadow = true; hub.add(wheel); actor.add(hub);
  }
}
