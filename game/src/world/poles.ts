import * as THREE from 'three/webgpu';
import type { PhysicsClient } from '../physics/client';
import type { PoleSpec, PoleState } from '../physics/protocol';
import type { VisualTiles } from './tiles';

interface PoleVisual { node: THREE.Object3D; model: THREE.Object3D; spec: PoleSpec; initial: THREE.Quaternion }

/** Mirrors per-object props nodes into sleeping Rapier bodies. IDs survive tile eviction. */
export class BreakablePoles {
  private visuals = new Map<string, PoleVisual>();
  readonly broken = new Set<string>();
  private position = new THREE.Vector3();
  private rotation = new THREE.Quaternion();
  private offset = new THREE.Vector3();
  private parentRotation = new THREE.Quaternion();
  private onLoad = (e: { scene: THREE.Object3D }) => this.addModel(e.scene);
  private onDispose = (e: { scene: THREE.Object3D }) => this.removeModel(e.scene);
  constructor(private readonly tiles: VisualTiles, private readonly physics: PhysicsClient,
    private readonly onLocalBreak: (id: string) => void) {
    tiles.events.addEventListener('load-model', this.onLoad);
    tiles.events.addEventListener('dispose-model', this.onDispose);
    this.addModel(tiles.tiles.group);
  }
  private addModel(model: THREE.Object3D) {
    const add: PoleSpec[] = [];
    model.updateMatrixWorld(true);
    model.traverse(node => {
      const e = node.userData;
      if (e?.prop_type !== 'sign' && e?.prop_type !== 'bus_stop') return;
      const tile = e.tile as string | undefined;
      const object = e.object_id as string | undefined;
      const id = (e.pole_id as string | undefined) ?? (tile && object ? `${tile}:${object}` : null);
      if (!id || this.visuals.has(id)) return;
      const base = node.getWorldPosition(new THREE.Vector3());
      const spec = { id, base: { x: base.x, y: base.y, z: base.z }, yaw: node.rotation.y, height: Number(e.pole_height) || 2.7 };
      this.visuals.set(id, { node, model, spec, initial: node.getWorldQuaternion(new THREE.Quaternion()) });
      add.push(spec);
    });
    this.physics.setPoles(add, []);
    for (const spec of add) if (this.broken.has(spec.id)) this.physics.breakPole(spec.id);
  }
  private removeModel(model: THREE.Object3D) {
    const remove: string[] = [];
    for (const [id, visual] of this.visuals) {
      let parent: THREE.Object3D | null = visual.node;
      while (parent && parent !== model) parent = parent.parent;
      if (parent === model) { this.visuals.delete(id); remove.push(id); }
    }
    this.physics.setPoles([], remove);
  }
  breakRemote(id: string) { this.broken.add(id); this.physics.breakPole(id); }
  setBroken(ids: readonly string[]) { for (const id of ids) this.breakRemote(id); }
  update(states: readonly PoleState[]) {
    for (const state of states) {
      if (state.brokeNow && !this.broken.has(state.id)) {
        this.broken.add(state.id);
        this.onLocalBreak(state.id);
      }
      const visual = this.visuals.get(state.id);
      if (!visual || !state.broken) continue;
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
    this.tiles.events.removeEventListener('load-model', this.onLoad);
    this.tiles.events.removeEventListener('dispose-model', this.onDispose);
    this.physics.setPoles([], [...this.visuals.keys()]);
    this.visuals.clear();
  }
}
