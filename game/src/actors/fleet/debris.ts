import * as THREE from 'three/webgpu';
import type { PhysicsClient } from '../../physics/client';
import type { DebrisState } from '../../physics/protocol';
import { DEBRIS } from '../../physics/constants';
import type { PartName } from '../../vehicle/damage';
import { shareActorPaint } from '../parkedPaint';
import { onPartDetached, type DetachEvent } from './damageRig';
import { fleetMaterial } from './material';

// Car parts that came off (vehicle/README.md "Loose and detached parts"). Whenever a drawn car's damage state shows a
// part newly detached (damageRig.ts), its drawn shape is copied into a mesh of its own and the physics worker gets a
// box body for it (world.ts spawnDebris: at most DEBRIS.cap pieces, each for DEBRIS.lifetimeS). Every client spawns
// its own debris from the shared damage state; nothing about a piece is synced after that.

/** Approximate part masses (kg): plastic bumper covers with their beams, steel bonnet and boot lid, a mirror. */
const MASS: Record<PartName, number> = { frontBumper: 7, rearBumper: 7, bonnet: 14, bootLid: 11, mirrorLeft: 1.5, mirrorRight: 1.5 };

export class DebrisField {
  private readonly pieces = new Map<string, { mesh: THREE.Mesh; born: number; part: PartName }>();
  private serial = 0;
  constructor(private readonly scene: THREE.Object3D, private readonly physics: PhysicsClient) {
    onPartDetached(event => this.spawn(event));
  }
  private spawn(event: DetachEvent) {
    const geometry = event.rig.partGeometry(event.body.geometry, event.info, event.part);
    geometry.computeBoundingBox();
    const box = geometry.boundingBox!, centre = box.getCenter(new THREE.Vector3()), half = box.getSize(new THREE.Vector3()).multiplyScalar(.5);
    geometry.translate(-centre.x, -centre.y, -centre.z);
    geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, fleetMaterial());
    // The shared fleet program reads the paint per object (parkedPaint.ts): an unpainted mesh would not draw.
    if (!shareActorPaint(event.body, mesh)) { geometry.dispose(); return; }
    mesh.castShadow = true; mesh.receiveShadow = true;
    event.body.updateWorldMatrix(true, false);
    new THREE.Matrix4().multiplyMatrices(event.body.matrixWorld, new THREE.Matrix4().makeTranslation(centre.x, centre.y, centre.z))
      .decompose(mesh.position, mesh.quaternion, new THREE.Vector3());
    const id = `debris-${++this.serial}`, part = event.info.name;
    mesh.name = `debris/${part}/${this.serial}`;
    // A light kick away from the car's middle and up, so the piece falls clear of the body it came off.
    const out = centre.clone().setY(0).normalize().applyQuaternion(mesh.quaternion), v = event.velocity;
    const spin = () => (Math.random() - .5) * 2;
    this.physics.spawnDebris({ id, pos: { x: mesh.position.x, y: mesh.position.y, z: mesh.position.z },
      rot: { x: mesh.quaternion.x, y: mesh.quaternion.y, z: mesh.quaternion.z, w: mesh.quaternion.w },
      half: { x: half.x, y: half.y, z: half.z }, mass: MASS[part],
      linvel: { x: v.x + out.x * 1.2, y: v.y + 1, z: v.z + out.z * 1.2 }, angvel: { x: spin(), y: spin(), z: spin() } });
    this.scene.add(mesh);
    this.pieces.set(id, { mesh, born: performance.now(), part });
    // The worker drops the oldest piece at the cap; drop its mesh now too.
    while (this.pieces.size > DEBRIS.cap) this.remove(this.pieces.keys().next().value!);
  }
  /** Follows the worker's debris bodies; a piece gone from them (expired, over the cap) goes. */
  update(states: readonly DebrisState[] | undefined, now = performance.now()) {
    const present = new Set<string>();
    for (const s of states ?? []) {
      const piece = this.pieces.get(s.id);
      if (!piece) continue;
      present.add(s.id);
      piece.mesh.position.set(s.pos.x, s.pos.y, s.pos.z);
      piece.mesh.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    }
    // A just-spawned piece is not in the states until the worker's next snapshot.
    for (const [id, piece] of this.pieces) if (!present.has(id) && now - piece.born > 1500) this.remove(id);
  }
  private remove(id: string) {
    const piece = this.pieces.get(id);
    if (!piece) return;
    piece.mesh.removeFromParent(); piece.mesh.geometry.dispose();
    this.pieces.delete(id);
  }
  /** Pieces now, for the developer console and probes. */
  list() { return [...this.pieces].map(([id, p]) => ({ id, part: p.part, position: p.mesh.position.toArray().map(v => Math.round(v * 100) / 100) })); }
  dispose() { onPartDetached(null); for (const id of [...this.pieces.keys()]) this.remove(id); }
}
