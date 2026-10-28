import type * as THREE from 'three/webgpu';
import type { Tile } from '3d-tiles-renderer';
import { prepareDetached } from './prepareDetached';

type Resources = { materials: THREE.Material[]; geometry: THREE.BufferGeometry[]; textures: THREE.Texture[] };
type Parser = { parseTile(buffer: ArrayBuffer, tile: Tile, extension: string, url: string, signal: AbortSignal): Promise<void> };

/** Native processTileModel keeps engineData.scene private until preparation finishes. Retain glTF resource ownership. */
export function prepareTiles(renderer: Parser & { group: THREE.Object3D; registerPlugin(plugin: object): void },
  prepare: (model: THREE.Object3D, tile: Tile, signal: AbortSignal) => Promise<void>,
  release: (model: THREE.Object3D, tile: Tile) => void) {
  const pending = new Map<Tile, { signal: AbortSignal; resources?: Resources; model?: THREE.Object3D }>();
  renderer.registerPlugin({
    name: 'TURKU_prepare_tile',
    processTileModel: async (model: THREE.Object3D, tile: Tile) => {
      const entry = pending.get(tile)!;
      entry.model = model;
      const materials = new Set<THREE.Material>(), geometry = new Set<THREE.BufferGeometry>(), textures = new Set<THREE.Texture>();
      model.traverse(object => {
        const mesh = object as THREE.Mesh;
        if (!mesh.isMesh) return;
        geometry.add(mesh.geometry);
        for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          materials.add(material);
          for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        }
      });
      entry.resources = { materials: [...materials], geometry: [...geometry], textures: [...textures] };
      await prepareDetached(model, renderer.group, () => prepare(model, tile, entry.signal));
    },
  });
  const parse = renderer.parseTile.bind(renderer);
  renderer.parseTile = async (buffer, tile, extension, url, signal) => {
    const entry = { signal, resources: undefined as Resources | undefined, model: undefined as THREE.Object3D | undefined };
    let complete = false;
    pending.set(tile, entry);
    try {
      await parse(buffer, tile, extension, url, signal);
      if (entry.resources && !signal.aborted) {
        Object.assign((tile as Tile & { engineData: Resources }).engineData, entry.resources);
        complete = true;
      }
    } finally {
      pending.delete(tile);
      if (!complete && entry.model) {
        release(entry.model, tile);
        for (const material of entry.resources?.materials ?? []) material.dispose();
        for (const geometry of entry.resources?.geometry ?? []) geometry.dispose();
        for (const texture of entry.resources?.textures ?? []) {
          if (typeof ImageBitmap !== 'undefined' && texture.image instanceof ImageBitmap) texture.image.close();
          texture.dispose();
        }
      }
    }
  };
}
