import * as THREE from 'three/webgpu';
import { vehicleLamps } from '../vehicle/lamps';
import type { VehicleState } from '../physics/protocol';
import { CHASSIS_LIFT } from '../traffic/constants';
import { FLEET_BY_ID, PLAYER_COLOR, PLAYER_MODEL } from './fleet/catalog';
import { loadAuthoredFleet, loadAuthoredFleetParts } from './fleet/authored';
import { fleetGlassMaterial, fleetMaterial } from './fleet/material';
import { addAuthoredFleetWheels, applyFleetWheels } from './fleet/wheels';
import { copyActorPaint, paintActor } from './parkedPaint';
import { DamageRig, deformedBounds, modelLayout, skinToCage } from './fleet/damageRig';

// Concurrent consumers (player, drive replays, remote players' starter cars) share one prepared model;
// Object3D clones keep their own wheel transforms and share the immutable geometry.
const prototypes = new Map<string, Promise<THREE.Group>>();

async function prepare(id: string, color: string): Promise<THREE.Group> {
  const model = FLEET_BY_ID.get(id);
  if (!model) throw new Error(`Unknown fleet model ${id}`);
  const [parts, glass] = await Promise.all([loadAuthoredFleetParts(id, 0), loadAuthoredFleet(id, 0, 'glass')]);
  if (!parts) throw new Error(`Fleet model ${id} has no articulated parts`);
  // Body and glazing are skinned to the damage cage (fleet/damageRig.ts); each clone binds its own rig in load().
  const layout = modelLayout(model);
  skinToCage(parts.body, layout);
  const root = new THREE.Group();
  const body = new THREE.SkinnedMesh(parts.body, fleetMaterial()); paintActor(body, color);
  body.name = 'fleet-actor'; body.castShadow = true; body.receiveShadow = true;
  if (glass) {
    skinToCage(glass, layout);
    const windows = new THREE.SkinnedMesh(glass, fleetGlassMaterial()); windows.name = 'fleet-glass'; windows.receiveShadow = true; body.add(windows);
  }
  addAuthoredFleetWheels(body, model, parts.wheels, fleetMaterial());
  // Fleet meshes face +Z with the tyres on Y=0; the chassis frame faces +X with its centre CHASSIS_LIFT up.
  body.rotation.y = Math.PI / 2; body.position.y = -CHASSIS_LIFT;
  root.add(body);
  return root;
}

/** The starter car (actors/fleet/catalog.ts PLAYER_MODEL), a real fleet model with articulated wheels. */
export class CarVisual {
  readonly root = new THREE.Group();
  private body: THREE.SkinnedMesh | undefined;
  /** The body's damage cage pose (vehicle/damage.ts), following the physics state's `damage`. */
  rig: DamageRig | undefined;
  loaded = false;

  constructor(scene: THREE.Object3D, private readonly model = PLAYER_MODEL, private readonly color = PLAYER_COLOR) {
    this.root.name = 'car'; scene.add(this.root);
  }

  async load() {
    const key = `${this.model}/${this.color}`;
    let pending = prototypes.get(key);
    if (!pending) {
      pending = prepare(this.model, this.color).catch(error => { prototypes.delete(key); throw error; });
      prototypes.set(key, pending);
    }
    const prototype = await pending, instance = prototype.clone(true);
    copyActorPaint(prototype, instance);
    this.body = instance.getObjectByName('fleet-actor') as THREE.SkinnedMesh;
    this.rig = new DamageRig(modelLayout(FLEET_BY_ID.get(this.model)!));
    this.rig.bind(this.body);
    const glass = this.body.getObjectByName('fleet-glass') as THREE.SkinnedMesh | undefined;
    if (glass) this.rig.bind(glass);
    this.rig.pose();
    this.root.clear(); this.root.add(instance);
    this.loaded = true;
  }

  update(s: VehicleState) {
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    // The fleet's own lamp tags light up (vehicle/lamps.ts); the root has no lamp meshes of its own.
    if (this.body) vehicleLamps(this.body, 'car', s.lights ?? 0);
    if (this.body) applyFleetWheels(this.body, s.wheels);
    this.rig?.show(s.damage);
    if (this.body) this.rig?.placeWheels(this.body);
  }

  /** The body's bounds in its model frame (+Z forward) as drawn now, and undeformed; null before load. */
  bounds() {
    if (!this.body) return null;
    return { deformed: deformedBounds(this.body), fresh: this.body.geometry.boundingBox!.clone() };
  }
}

export { ParkedCarVisuals, type ParkedRest, type TrafficInstance } from './fleet/visuals';
