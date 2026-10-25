// Object-ID pass for the visual QA loop (qa/README.md §2, pass=id): every mesh is drawn unlit in a flat colour
// whose 24-bit value R<<16|G<<8|B is an object id; 0 (black) = sky/nothing. window.__qaIdTable maps id -> object.
// Ids come from glTF node extras ({kind, id, tile, material}, docs/specs/tiles.md §3.1); meshes without extras
// get kind 'other' and their node name.
import * as THREE from 'three/webgpu';

interface IdEntry { kind: string; id: string; tile: string | null; material?: string; facade?: string; building?: string; layer?: string }

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
      const replace = (original: THREE.Material, data: Record<string, unknown>) => {
        const kind = typeof data.kind === 'string' && KINDS.has(data.kind) ? data.kind : layerKinds[String(data.layer)] ?? 'other';
        const entry: IdEntry = { kind, id: String(data.object_id ?? data.id ?? (m.name || 'mesh')),
          tile: typeof data.tile === 'string' ? data.tile : tileId };
        if (data.layer != null) entry.layer = String(data.layer);
        if (data.building_id != null) entry.building = String(data.building_id);
        if (data.facade_index != null || data.facade != null) entry.facade = String(data.facade_index ?? data.facade);
        const material = original.userData.material_key ?? data.material_key ?? data.material ?? original.name;
        const item = { ...entry, ...(material ? { material: String(material) } : {}) };
        const mat = new THREE.MeshBasicNodeMaterial({ color: IdPass.color(this.idFor(item)), side: original.side });
        mat.toneMapped = false;
        mat.fog = false;
        return mat;
      };
      if (Array.isArray(src.objects)) {
        const original = Array.isArray(m.material) ? m.material[0] : m.material;
        m.geometry = m.geometry.clone();
        m.geometry.clearGroups();
        m.material = src.objects.map((object: Record<string, unknown>, index: number) => {
          m.geometry.addGroup(Number(object.start), Number(object.count), index);
          return replace(original, object);
        });
      } else {
        m.material = Array.isArray(m.material) ? m.material.map(mat => replace(mat, src)) : replace(m.material, src);
      }
      m.castShadow = m.receiveShadow = false;
    });
  }
}
