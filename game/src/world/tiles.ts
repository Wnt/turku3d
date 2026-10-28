// Resident draft model ownership and telemetry; loading belongs to CoarseTiles.
import * as THREE from 'three/webgpu';
import { batchTileShadows } from './shadowBatch';
import { disposeShadowBatch } from './shadowChunks';

type ModelEvent = { scene: THREE.Object3D; tile: { extras?: Record<string, unknown> } };
interface TileEvents { 'load-model': ModelEvent; 'dispose-model': ModelEvent }

export class VisualTiles {
  readonly events = new THREE.EventDispatcher<TileEvents>();
  readonly modelEvents = this.events;
  readonly models = new Set<THREE.Object3D>();
  residentTris = 0;
  residentModels = 0;
  loadedModels = 0;
  private counts = new Map<THREE.Object3D, number>();
  private shadows = new Map<THREE.Object3D, THREE.Mesh>();

  constructor(private idPass = false) {}

  trackModel(scene: THREE.Object3D, id: string) {
    if (this.models.has(scene)) return;
    let tris = 0;
    scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true; mesh.receiveShadow = true;
      tris += (mesh.geometry.index?.count ?? mesh.geometry.attributes.position.count) / 3;
    });
    if (!this.idPass) {
      const shadow = batchTileShadows(scene);
      if (shadow) this.shadows.set(scene, shadow);
    }
    this.models.add(scene); this.counts.set(scene, tris);
    this.residentTris += tris; this.residentModels++; this.loadedModels++;
    this.events.dispatchEvent({ type: 'load-model', scene, tile: { extras: { id } } });
  }

  untrackModel(scene: THREE.Object3D, id: string) {
    if (!this.models.delete(scene)) return;
    const shadow = this.shadows.get(scene);
    if (shadow) { disposeShadowBatch(shadow); this.shadows.delete(scene); }
    this.residentTris -= this.counts.get(scene) ?? 0; this.counts.delete(scene);
    this.residentModels--;
    this.events.dispatchEvent({ type: 'dispose-model', scene, tile: { extras: { id } } });
  }
}
