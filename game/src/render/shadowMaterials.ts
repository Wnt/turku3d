import * as THREE from 'three/webgpu';
import { NormalPrepass } from './normalPrepass';
import { ownNodeAttributes } from './nodeAttributes';
import { instancePrograms } from './instancePrograms';

type ShadowMaterial = THREE.Material & { isShadowPassMaterial?: boolean };

/** Keep opaque and cutout shadow programs separate. r186 copies each caster's alphaTest into the override;
 * alternating zero/nonzero bumps that shared material's version and rehashes every caster every frame. */
export class ShadowMaterials {
  private masked = new WeakMap<THREE.Material, { material: ShadowMaterial; version: number }>();
  select(source: THREE.Material, override: THREE.Material | null): THREE.Material | null {
    if (!override || !(override as ShadowMaterial).isShadowPassMaterial || source.alphaTest <= 0) return override;
    let cached = this.masked.get(override);
    if (!cached) {
      cached = { material: override.clone(), version: override.version };
      this.masked.set(override, cached);
      const material = cached.material;
      override.addEventListener('dispose', () => { material.dispose(); this.masked.delete(override); });
    } else if (cached.version !== override.version) {
      cached.material.copy(override);
      cached.version = override.version;
    }
    cached.material.isShadowPassMaterial = true;
    return cached.material;
  }
}

type Draw = Parameters<THREE.WebGPURenderer['renderObject']>;
/** r186 accepts count === 0; WebGPU warns on every pass. Reject empty range intersections before encoding. */
export function hasDrawVertices(geometry: THREE.BufferGeometry, group: Draw[5]) {
  const range = geometry.drawRange;
  const first = Math.max(0, range.start, group?.start ?? 0);
  const last = Math.min(geometry.index?.count ?? geometry.attributes.position?.count ?? 0,
    range.start + range.count, group ? group.start + group.count : Infinity);
  return last > first;
}
export class GameRenderer extends THREE.WebGPURenderer {
  readonly normalPrepass = new NormalPrepass();
  private shadowMaterials = new ShadowMaterials();
  private readonly shadowContext = this.contextNode;
  constructor(parameters?: ConstructorParameters<typeof THREE.WebGPURenderer>[0]) {
    super(parameters);
    this.debug.onNodeBuilderCreated = (builder, draw) => instancePrograms(builder, (draw as { object: THREE.Object3D }).object);
  }
  private releaseAttributes?: () => void;
  override async init(): Promise<this> {
    await super.init();
    this.releaseAttributes ??= ownNodeAttributes(this);
    return this;
  }
  override dispose() { this.releaseAttributes?.(); this.normalPrepass.dispose(); return super.dispose(); }
  override renderObject(object: Draw[0], scene: Draw[1], camera: Draw[2], geometry: Draw[3], material: Draw[4],
    group: Draw[5], lights: Draw[6], clipping: Draw[7] = null, pass: Draw[8] = null) {
    if (!hasDrawVertices(geometry, group)) return;
    const original = scene.overrideMaterial;
    const context = this.contextNode;
    // Depth draws do not sample beauty-pass GTAO. Keep their context stable across beauty and reflection passes.
    if ((original as ShadowMaterial | null)?.isShadowPassMaterial) this.contextNode = this.shadowContext;
    scene.overrideMaterial = original?.userData.normalPrepass
      ? this.normalPrepass.select(material) : this.shadowMaterials.select(material, original);
    try { super.renderObject(object, scene, camera, geometry, material, group, lights, clipping, pass); }
    finally { scene.overrideMaterial = original; this.contextNode = context; }
  }
}
