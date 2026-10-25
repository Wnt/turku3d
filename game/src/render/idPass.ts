// Object-ID pass for the visual QA loop (qa/README.md §2, pass=id): every mesh is drawn unlit in a flat colour
// whose 24-bit value R<<16|G<<8|B is an object id; 0 (black) = sky/nothing. window.__qaIdTable maps id -> object.
// Ids come from glTF node extras ({kind, id, tile, material}, docs/specs/tiles.md §3.1); meshes without extras
// get kind 'other' and their node name.
import * as THREE from 'three/webgpu';

export interface IdEntry { kind: string; id: string; tile: string | null; material?: string; facade?: string; building?: string; layer?: string }

const KINDS = new Set(['building', 'ground', 'prop', 'vegetation', 'water', 'other']);

export class IdPass {
  readonly table: Record<string, IdEntry> = {};
  private byKey = new Map<string, number>();
  private next = 1;

  /** Renderer/scene state for an exact id image: no tone mapping, no sRGB encode, no sky/fog/shadows. */
  static prepare(renderer: THREE.WebGPURenderer, scene: THREE.Scene, hide: THREE.Object3D[]) {
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    renderer.shadowMap.enabled = false;
    scene.fog = null;
    scene.background = new THREE.Color(0, 0, 0);
    for (const o of hide) o.visible = false;
  }

  static color(n: number): THREE.Color {
    return new THREE.Color().setRGB(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, THREE.LinearSRGBColorSpace);
  }

  private idFor(e: IdEntry): number {
    const key = JSON.stringify(e);
    let n = this.byKey.get(key);
    if (n === undefined) {
      n = this.next++;
      this.byKey.set(key, n);
      this.table[String(n)] = e;
    }
    return n;
  }

  /** Replace the materials of a freshly loaded tile model. */
  apply(model: THREE.Object3D, tileId: string | null) {
    model.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      // Layer extras and package extras are inherited; nearest node wins.
      const parents: THREE.Object3D[] = [];
      for (let p: THREE.Object3D | null = m; p; p = p.parent) {
        parents.unshift(p);
        if (p === model) break;
      }
      const src = Object.assign({}, ...parents.map(p => p.userData)) as Record<string, unknown>;
      const layerKinds: Record<string, string> = { buildings: 'building', ground: 'ground', vegetation: 'vegetation', props: 'prop' };
      const kind = typeof src.kind === 'string' && KINDS.has(src.kind) ? src.kind : layerKinds[String(src.layer)] ?? 'other';
      const entry: IdEntry = { kind, id: String(src.object_id ?? src.id ?? (m.name || 'mesh')),
        tile: typeof src.tile === 'string' ? src.tile : tileId };
      if (src.layer != null) entry.layer = String(src.layer);
      if (src.building_id != null) entry.building = String(src.building_id);
      if (src.facade_index != null || src.facade != null) entry.facade = String(src.facade_index ?? src.facade);
      const replace = (original: THREE.Material) => {
        const material = original.userData.material_key ?? src.material_key ?? src.material ?? original.name;
        const item = { ...entry, ...(material ? { material: String(material) } : {}) };
        const mat = new THREE.MeshBasicNodeMaterial({ color: IdPass.color(this.idFor(item)), side: original.side });
        mat.toneMapped = false;
        mat.fog = false;
        return mat;
      };
      m.material = Array.isArray(m.material) ? m.material.map(replace) : replace(m.material);
      m.castShadow = m.receiveShadow = false;
    });
  }
}
