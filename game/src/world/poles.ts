import * as THREE from 'three/webgpu';
import type { PhysicsClient } from '../physics/client';
import type { PoleSpec, PoleState } from '../physics/protocol';
import type { VisualTiles } from './tiles';
import { extractPoles, PoleRanges, type PoleCandidate } from './poleExtraction';

interface PoleVisual { candidate: PoleCandidate; node: THREE.Object3D; model: THREE.Object3D; spec: PoleSpec; initial: THREE.Quaternion }

/** Mirrors resident full and draft sign nodes into nearby sleeping Rapier bodies. */
export class BreakablePoles {
  private visuals = new Map<string, PoleVisual>();
  private active = new Set<string>();
  private ranges = new PoleRanges();
  readonly broken = new Set<string>();
  private position = new THREE.Vector3();
  private rotation = new THREE.Quaternion();
  private offset = new THREE.Vector3();
  private parentRotation = new THREE.Quaternion();
  private onLoad = (e: { scene: THREE.Object3D }) => this.addModel(e.scene);
  private onDispose = (e: { scene: THREE.Object3D }) => this.removeModel(e.scene);
  constructor(private readonly tiles: VisualTiles, private readonly physics: PhysicsClient,
    private readonly onLocalBreak: (id: string) => void) {
    tiles.modelEvents.addEventListener('load-model', this.onLoad);
    tiles.modelEvents.addEventListener('dispose-model', this.onDispose);
    for (const model of tiles.models) this.addModel(model);
  }
  private addModel(model: THREE.Object3D) {
    for (const candidate of extractPoles(model)) {
      const { spec, node } = candidate;
      if (this.visuals.has(spec.id)) continue;
      this.visuals.set(spec.id, { candidate, node, model, spec, initial: node.getWorldQuaternion(new THREE.Quaternion()) });
    }
  }
  private removeModel(model: THREE.Object3D) {
    const remove: string[] = [];
    for (const [id, visual] of this.visuals) {
      if (visual.model !== model) continue;
      this.visuals.delete(id);
      if (this.active.delete(id)) remove.push(id);
    }
    if (remove.length) this.physics.setPoles([], remove);
  }
  breakRemote(id: string) { this.broken.add(id); this.physics.breakPole(id); }
  setBroken(ids: readonly string[]) { for (const id of ids) this.breakRemote(id); }
  /** Only poles inside the resident collision radius have bodies; a 20 m margin covers a car entering it. */
  private sync(focus: { x: number; z: number }, radius: number) {
    const add: PoleSpec[] = [], remove: string[] = [];
    for (const [id, visual] of this.visuals) {
      const p = visual.spec.base;
      const near = Math.hypot(focus.x - p.x, focus.z - p.z) <= radius + 20;
      if (near && !this.active.has(id)) { this.active.add(id); add.push(visual.spec); }
      else if (!near && this.active.delete(id)) remove.push(id);
    }
    if (add.length || remove.length) this.physics.setPoles(add, remove);
    for (const spec of add) if (this.broken.has(spec.id)) this.physics.breakPole(spec.id);
  }
  update(states: readonly PoleState[], focus: { x: number; z: number }, radius: number) {
    this.sync(focus, radius);
    for (const state of states) {
      if (state.brokeNow && !this.broken.has(state.id)) {
        this.broken.add(state.id);
        this.onLocalBreak(state.id);
      }
      const visual = this.visuals.get(state.id);
      if (!visual || !state.broken) continue;
      if (visual.candidate.range && visual.node === visual.candidate.node) {
        visual.initial.copy(visual.candidate.node.parent?.getWorldQuaternion(new THREE.Quaternion()) ?? new THREE.Quaternion());
        visual.node = this.ranges.detach(visual.candidate);
      }
      const { node, spec, initial } = visual;
      this.rotation.set(state.rot.x, state.rot.y, state.rot.z, state.rot.w);
      this.offset.set(0, -spec.height / 2, 0).applyQuaternion(this.rotation);
      this.position.set(state.pos.x, state.pos.y, state.pos.z).add(this.offset);
      node.parent?.worldToLocal(this.position);
      node.position.copy(this.position);
      node.parent?.getWorldQuaternion(this.parentRotation);
      node.quaternion.copy(this.parentRotation.invert().multiply(this.rotation).multiply(initial));
    }
  }
  dispose() {
    this.tiles.modelEvents.removeEventListener('load-model', this.onLoad);
    this.tiles.modelEvents.removeEventListener('dispose-model', this.onDispose);
    this.physics.setPoles([], [...this.active]);
    this.active.clear(); this.visuals.clear();
  }
}
