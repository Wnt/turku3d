import * as T from 'three/webgpu';
import { TRUCK } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import { disposeDraw } from '../render/sharedMaterials';
import { racerModel } from './prerunner/model';
import { applyRaceWheels, createRaceRig } from './prerunner/rig';
import { createRaceWheels } from './prerunner/wheels';

/** Original KORPI 440 racer. Immutable shared meshes; per-actor wheel and suspension matrices. */
export class TruckVisual {
  readonly root = new T.Group();
  readonly seat = new T.Object3D();
  loaded = false;

  constructor(scene: T.Object3D) {
    this.root.name = 'truck'; this.root.visible = false;
    this.seat.position.set(-.05, 0, -.37); this.seat.rotation.y = Math.PI / 2;
    this.root.add(this.seat); scene.add(this.root);
  }

  async load() {
    if (this.loaded) return;
    const body = new T.LOD(); body.name = 'korpi-body';
    for (let lod = 0; lod < 3; lod++) {
      const model = racerModel(lod).body.clone(true);
      model.getObjectByName('korpi-spare')?.removeFromParent();
      body.addLevel(model, [0, 35, 90][lod], .1);
    }
    this.root.add(body);
    for (let i = 0; i < TRUCK.wheels.length; i++) {
      const pivot = new T.Group(); pivot.name = `vehicle-wheel-${i}`;
      pivot.add(new T.Object3D()); this.root.add(pivot);
    }
    this.root.add(createRaceWheels());
    // Tiny running-gear details disappear beyond 35m; wheel centres remain physical at every LOD.
    const suspension = new T.LOD(); suspension.name = 'korpi-running-gear';
    suspension.addLevel(createRaceRig(), 0); suspension.addLevel(new T.Group(), 35, .1);
    this.root.add(suspension);
    this.root.traverse(o => { if ((o as T.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    const sag = 9.81 / (4 * TRUCK.suspensionStiffness);
    applyRaceWheels(this.root, TRUCK.wheels.map(at => ({ pos: { x: at.x, y: at.y - TRUCK.rest + sag, z: at.z }, steer: 0, rotation: 0 })));
    this.loaded = true;
  }

  dispose() {
    this.root.removeFromParent();
    this.root.traverse(object => {
      if ((object as T.Mesh).isMesh) disposeDraw(object as T.Mesh);
      // Only instance matrices belong to this actor. Geometry and materials belong to the library.
      if ((object as T.InstancedMesh).isInstancedMesh) (object as T.InstancedMesh).dispose();
    });
  }

  update(s: VehicleState) {
    this.root.visible = this.loaded;
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    applyRaceWheels(this.root, s.wheels);
  }
}
