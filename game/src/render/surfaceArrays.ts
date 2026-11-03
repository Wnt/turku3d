import * as THREE from 'three/webgpu';
import { attribute, float, int, mix, normalMap, positionWorld, select, texture, uniformArray, vec2, vec3, vec4 } from 'three/tsl';
import { QUALITY, type QualityTier } from './quality';
import { wetSurface } from './weather/surfaces';
import { keepTextureData } from '../world/sharedAssets';

// Census at spawn (16:02): each tile drew ~25 meshes, one per distinct ground/surface material. The coarse
// generator's wet library surfaces (asphalt, granite curb, cobblestone, concrete slabs, quay stone) share one
// texture layout (albedo 1024² ETC1S, normal 512² UASTC, ORM 256² UASTC, the same UV transform), so a tile's
// ground draws them as ONE mesh: a per-vertex layer selects the slice of city-wide texture arrays assembled from
// the very same compressed mips, and per-layer factors reproduce MaterialStyler's per-kind styling.

/** MaterialStyler kinds that render identically apart from texture and grain frequency (render/materials.ts). */
const GRAIN: Record<string, number> = { asphalt: 4, granite: 2.5, cobblestone: 2 };
const WET_STANDARD = /(?:concrete|stone|brick|road_paint|fieldstone)/;
export const SURFACE_LAYER = '_surface_layer';
const MAX_LAYERS = 64;
/** Room for the coarse library's surfaces without growth rebuilds (a layer is ~1.8 MB: 1024² albedo, 512² normal, 256² ORM). */
const MIN_CAPACITY = 16;
const capacityFor = (layers: number) => Math.max(MIN_CAPACITY, 2 ** Math.ceil(Math.log2(layers)));

type Compressed = THREE.CompressedTexture & { mipmaps: { data: ArrayBufferView; width: number; height: number }[] };
export interface SurfaceSlice { key: string; maps: [Compressed, Compressed, Compressed]; grain: number; wet: boolean; patches: number;
  color: THREE.Color; roughness: number; metalness: number; ao: number; normalScale: THREE.Vector2 }

const ATTRIBUTES = ['color_1', 'normal', 'position', 'uv'];
function compressed(value: THREE.Texture | null): value is Compressed {
  const texture = value as Compressed | null;
  return !!texture && (texture as THREE.CompressedTexture).isCompressedTexture === true && !(texture as THREE.CompressedArrayTexture).isCompressedArrayTexture
    && typeof texture.userData.sharedAsset === 'string' && Array.isArray(texture.mipmaps) && texture.mipmaps.length > 0
    && texture.mipmaps.every(mip => mip.data && ArrayBuffer.isView(mip.data) && mip.data.byteLength > 0) && texture.channel === 0;
}
const arrayNode = (array: THREE.CompressedArrayTexture) => texture(array);
const transform = (t: THREE.Texture) => [...t.offset.toArray(), ...t.repeat.toArray(), ...t.center.toArray(), t.rotation].join(',');
/** Everything that must match for two textures to be slices of one array. */
const layout = (t: Compressed) => JSON.stringify([t.format, t.type, t.colorSpace, t.wrapS, t.wrapT, t.magFilter, t.minFilter, t.flipY,
  t.mipmaps.map(m => [m.width, m.height, m.data.byteLength]), transform(t)]);

/** The slice for a mesh whose material MaterialStyler styles as a library surface; null keeps it apart.
 * Wet: asphalt, granite and cobbles (grain) and wet standard surfaces; dry: grass (grain and colour patches,
 * no wet film or ground lamp light) and plain standard surfaces such as gravel (MaterialStyler leaves them as is). */
export function surfaceSlice(mesh: THREE.Mesh, kind: string, key: string): SurfaceSlice | null {
  const material = mesh.material as THREE.MeshStandardMaterial;
  if (Array.isArray(mesh.material) || !material.isMeshStandardMaterial || (material as unknown as THREE.NodeMaterial).isNodeMaterial) return null;
  if (key.startsWith('night_') || key === 'draft_quay_stone_lit' || key === 'facade_kit') return null;
  let grain = 0, wet = false, patches = 0;
  if (GRAIN[kind] !== undefined) { grain = GRAIN[kind]; wet = true; }
  else if (kind === 'grass') { grain = 1.4; patches = 1; }
  else if (kind === 'standard') wet = WET_STANDARD.test(key);
  else return null;
  if (material.transparent || material.alphaTest > 0 || material.side !== THREE.FrontSide || material.vertexColors
    || material.emissiveMap || material.lightMap || material.bumpMap || material.displacementMap || material.alphaMap
    || material.emissive.getHex() !== 0 || material.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile) return null;
  const orm = material.roughnessMap;
  if (!compressed(material.map) || !compressed(material.normalMap) || !compressed(orm) || material.metalnessMap !== orm
    || (material.aoMap && material.aoMap !== orm)) return null;
  const geometry = mesh.geometry;
  if (!geometry.index || geometry.groups.length || Object.keys(geometry.morphAttributes).length || geometry.drawRange.start !== 0
    || geometry.drawRange.count !== Infinity || Object.keys(geometry.attributes).sort().join() !== ATTRIBUTES.join()) return null;
  if ((mesh as THREE.InstancedMesh).isInstancedMesh || mesh.children.length) return null;
  if (mesh.userData.prop_type || (mesh.userData.objects as Record<string, unknown>[] | undefined)?.some(row => row.prop_type)) return null;
  return { key: [material.map, material.normalMap, orm].map(t => t.userData.sharedAsset).join('|'), maps: [material.map, material.normalMap, orm],
    grain, wet, patches, color: material.color.clone(), roughness: material.roughness, metalness: material.metalness,
    ao: material.aoMap ? material.aoMapIntensity : 0, normalScale: material.normalScale.clone() };
}

/** City-wide texture arrays (albedo, normal, ORM) and the one shared surface material that samples them. */
export class SurfaceArrays {
  private keys = new Map<string, number>();
  private slices: SurfaceSlice[] = [];
  private layouts: string[] | null = null;
  private arrays: THREE.CompressedArrayTexture[] = [];
  private nodes: ReturnType<typeof arrayNode>[] = [];
  private factors = [Array.from({ length: MAX_LAYERS }, () => new THREE.Vector4()), Array.from({ length: MAX_LAYERS }, () => new THREE.Vector4()),
    Array.from({ length: MAX_LAYERS }, () => new THREE.Vector4())];
  private materials = new Map<boolean, THREE.MeshStandardNodeMaterial>();
  private pending: Promise<void> = Promise.resolve();
  private writes = new Set<number>();
  private scheduled = false;
  /** upload: a complete texture through the budgeted queue; update: change and upload a texture in one budgeted
   * after-frame task (render/textureUploads.ts). */
  constructor(private tier: QualityTier, private detail: THREE.Texture,
    private upload: (texture: THREE.Texture) => Promise<void> = async () => {},
    private update: (texture: THREE.Texture, bytes: number, change: () => void) => Promise<void> = async (_texture, _bytes, change) => change()) {}

  owns(material: THREE.Material) { return [...this.materials.values()].includes(material as THREE.MeshStandardNodeMaterial); }
  get layers() { return this.slices.length; }

  /** The array layer of a slice, adding it (and growing the arrays) when new; null if its layout differs. */
  layer(slice: SurfaceSlice): number | null {
    const known = this.keys.get(slice.key);
    if (known !== undefined) return known;
    const layouts = slice.maps.map(layout);
    if (this.layouts && layouts.some((value, i) => value !== this.layouts![i])) return null;
    if (this.slices.length >= MAX_LAYERS) return null;
    this.layouts ??= layouts;
    const index = this.slices.length;
    this.slices.push(slice); this.keys.set(slice.key, index);
    const [a, b, c] = this.factors;
    a[index].set(slice.color.r, slice.color.g, slice.color.b, slice.grain);
    b[index].set(slice.roughness, slice.metalness, slice.ao, slice.patches);
    c[index].set(slice.normalScale.x, slice.normalScale.y, 0, 0);
    this.rebuild();
    return index;
  }

  /** Nothing a resident tile samples changes inside a frame. The first set is built at once (before anything draws
   * it: the first material asks for it) and uploads with the tile's preparation. A later slice is written in place: one budgeted after-frame task
   * copies its layer and uploads just that layer (r186 uploads layerUpdates alone, so only after the set's first full
   * upload). Only growth past the capacity builds a complete larger set, uploaded before the material swaps to it. */
  private rebuild() {
    if (!this.nodes.length) return; // nothing samples the arrays yet: the first material builds them (all slices so far)
    this.writes.add(this.slices.length - 1);
    if (this.scheduled) return;
    this.scheduled = true;
    this.pending = this.pending.then(async () => {
      this.scheduled = false;
      const layers = [...this.writes];
      this.writes.clear();
      if (capacityFor(this.slices.length) > this.arrays[0].image.depth) {
        const next = this.build();
        await Promise.all(next.map(texture => this.upload(texture)));
        const previous = this.arrays;
        this.arrays = next;
        next.forEach((texture, channel) => { if (this.nodes[channel]) this.nodes[channel].value = texture; }); // every material
        for (const texture of previous) texture.dispose();
        return;
      }
      await Promise.all(this.arrays.map((array, channel) => {
        const bytes = layers.length * this.slices[0].maps[channel].mipmaps.reduce((sum, mip) => sum + mip.data.byteLength, 0);
        return this.update(array, bytes, () => {
          for (const index of layers) { this.copy(array, index, channel); array.addLayerUpdate(index); }
          array.needsUpdate = true;
        });
      }));
    }).catch(error => console.warn(`[surface arrays] ${error}`));
  }

  private first() {
    this.arrays = this.build();
    const first = this.arrays;
    this.pending = this.pending.then(() => Promise.all(first.map(texture => this.upload(texture)))).then(() => {});
  }

  /** Resolves once every layer handed out so far is in the arrays the material samples (MaterialStyler.prepare). */
  ready() { return this.pending; }

  /** Every current slice copied from its own compressed mips. A new set is never given layerUpdates: r186 uploads
   * only the listed layers even on a texture's first upload (the others stay black). */
  private build() {
    const capacity = capacityFor(this.slices.length);
    return [0, 1, 2].map(channel => {
      const first = this.slices[0].maps[channel];
      const mipmaps = first.mipmaps.map(mip => ({ data: new Uint8Array(mip.data.byteLength * capacity), width: mip.width, height: mip.height }));
      const next = new THREE.CompressedArrayTexture(mipmaps as unknown as THREE.CompressedTextureMipmap[], first.image.width, first.image.height, capacity, first.format, first.type);
      for (const property of ['colorSpace', 'wrapS', 'wrapT', 'magFilter', 'minFilter', 'flipY', 'premultiplyAlpha', 'unpackAlignment'] as const)
        (next as unknown as Record<string, unknown>)[property] = first[property];
      next.anisotropy = first.anisotropy; // the sources' sampling, so the look is unchanged
      next.offset.copy(first.offset); next.repeat.copy(first.repeat); next.center.copy(first.center); next.rotation = first.rotation;
      next.generateMipmaps = false; next.name = `surface-array-${channel}`;
      for (let i = 0; i < this.slices.length; i++) this.copy(next, i, channel);
      next.needsUpdate = true;
      return next;
    });
  }

  private copy(array: THREE.CompressedArrayTexture, index: number, channel: number) {
    const source = this.slices[index].maps[channel];
    array.mipmaps.forEach((mip, level) => {
      const data = source.mipmaps[level].data;
      (mip.data as Uint8Array).set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength), index * data.byteLength);
    });
  }

  /** One node material per wet/dry family for every merged tile surface: MaterialStyler's styling per layer. */
  surfaceMaterial(wet: boolean): THREE.MeshStandardNodeMaterial {
    const existing = this.materials.get(wet);
    if (existing) return existing;
    const material = new THREE.MeshStandardNodeMaterial({ name: wet ? 'surface-array' : 'surface-array-dry' });
    const layer = int(attribute(SURFACE_LAYER, 'float').add(0.5));
    // One base node per channel shared by the wet and dry materials (their .depth() clones read its value), so a
    // swap reaches both. Per-material nodes left the first family on the released set: black sidewalks and kerbs.
    if (!this.arrays.length) this.first();
    const [albedo, normal, orm] = [0, 1, 2].map(channel => this.nodes[channel] ??= arrayNode(this.arrays[channel]));
    const [colours, pbr, scales] = this.factors.map(values => uniformArray(values, 'vec4'));
    const element = (values: ReturnType<typeof uniformArray>) => values.element(layer) as unknown as THREE.Node<'vec4'>;
    const colour = vec4(element(colours)), surface = vec4(element(pbr)), scale = vec4(element(scales));
    const sampled = orm.depth(layer);
    const q = QUALITY[this.tier];
    // World coordinates remain continuous across tile boundaries (as MaterialStyler's grain); frequency 0: none.
    const frequency = colour.w;
    const grainUv = positionWorld.xz.add(positionWorld.y.mul(0.37)).mul(frequency);
    const grains = frequency.greaterThan(0);
    const grain = texture(this.detail, grainUv).r.sub(0.5).mul(select(grains, float(q.detail), float(0)));
    // Grass patches as MaterialStyler: a broad world-space tint mix, on the layers that carry it.
    const patches = mix(vec3(0.92, 0.93, 0.83), vec3(1.04, 1.02, 0.97), texture(this.detail, positionWorld.xz.mul(0.035)).r);
    material.colorNode = albedo.depth(layer).rgb.mul(colour.xyz).mul(grain.add(1)).mul(mix(vec3(1), patches, surface.w));
    const roughness = sampled.g.mul(surface.x);
    material.roughnessNode = select(grains, roughness.add(grain).clamp(0.45, 1), roughness);
    material.metalnessNode = sampled.b.mul(surface.y);
    material.aoNode = mix(float(1), sampled.r, surface.z);
    material.normalNode = normalMap(normal.depth(layer).rgb, vec2(scale.x, scale.y));
    if (wet) wetSurface(material);
    this.materials.set(wet, material);
    return material;
  }

  dispose() {
    for (const array of this.arrays) array.dispose();
    for (const material of this.materials.values()) material.dispose();
    this.arrays = []; this.nodes = []; this.materials.clear();
  }
}

const position = new THREE.Vector3(), normal = new THREE.Vector3(), normalMatrix = new THREE.Matrix3();
/** One mesh per parent for the merged slices; sources detach (restored by `restore`). `steps` yields after each
 * copied source, so a tile's ground merges across frames (MaterialStyler.applyAsync); a group's merged mesh replaces
 * its sources only once complete, and `restore` undoes the completed groups (also when stopped part-way). */
export function mergeSurfaceSteps(model: THREE.Object3D, arrays: SurfaceArrays, kindOf: (mesh: THREE.Mesh) => { kind: string; key: string } | null) {
  const merged: { mesh: THREE.Mesh; sources: THREE.Mesh[]; parent: THREE.Object3D }[] = [];
  function* steps(): Generator<void> {
    const groups = new Map<string, { parent: THREE.Object3D; wet: boolean; sources: { mesh: THREE.Mesh; layer: number }[] }>();
    model.updateWorldMatrix(true, true);
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || !mesh.parent) return;
      let owner: THREE.Object3D | null = mesh;
      while (owner && !owner.userData.layer) owner = owner.parent;
      if (owner?.userData.layer !== 'ground') return;
      // Ground maps feed the arrays now or in a later tile: their CPU mips stay after upload.
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        const standard = material as THREE.MeshStandardMaterial;
        keepTextureData(standard.map); keepTextureData(standard.normalMap); keepTextureData(standard.roughnessMap);
        keepTextureData(standard.metalnessMap); keepTextureData(standard.aoMap);
      }
      const style = kindOf(mesh);
      const slice = style && surfaceSlice(mesh, style.kind, style.key);
      const layer = slice && arrays.layer(slice);
      if (!slice || layer === null || layer === undefined) return;
      const group = `${mesh.parent.uuid}:${slice.wet}`;
      let entry = groups.get(group);
      if (!entry) groups.set(group, entry = { parent: mesh.parent, wet: slice.wet, sources: [] });
      entry.sources.push({ mesh, layer });
    });
    for (const { parent, wet, sources } of groups.values()) {
      if (sources.length < 2) continue;
      let vertices = 0, indices = 0;
      for (const { mesh } of sources) { vertices += mesh.geometry.attributes.position.count; indices += mesh.geometry.index!.count; }
      const geometry = new THREE.BufferGeometry();
      const first = sources[0].mesh.geometry;
      for (const name of ATTRIBUTES) {
        const source = first.getAttribute(name);
        const Array = (source.array as THREE.TypedArray).constructor as new (length: number) => THREE.TypedArray;
        geometry.setAttribute(name, new THREE.BufferAttribute(new Array(vertices * source.itemSize), source.itemSize, source.normalized));
      }
      const layers = new Float32Array(vertices);
      const index = vertices > 65535 ? new Uint32Array(indices) : new Uint16Array(indices);
      const rows: Record<string, unknown>[] = [];
      let vertex = 0, offset = 0;
      for (const { mesh, layer } of sources) {
        const g = mesh.geometry, count = g.attributes.position.count, identity = mesh.matrix.equals(new THREE.Matrix4());
        normalMatrix.getNormalMatrix(mesh.matrix);
        for (const name of ATTRIBUTES) {
          const from = g.getAttribute(name) as THREE.BufferAttribute | THREE.InterleavedBufferAttribute, to = geometry.getAttribute(name) as THREE.BufferAttribute;
          copyRaw(from, to, vertex, count);
          if (identity) continue;
          if (name === 'position') for (let i = vertex; i < vertex + count; i++) { position.fromBufferAttribute(to, i).applyMatrix4(mesh.matrix); to.setXYZ(i, position.x, position.y, position.z); }
          if (name === 'normal') for (let i = vertex; i < vertex + count; i++) { normal.fromBufferAttribute(to, i).applyMatrix3(normalMatrix).normalize(); to.setXYZ(i, normal.x, normal.y, normal.z); }
        }
        layers.fill(layer, vertex, vertex + count);
        const source = g.index!;
        for (let i = 0; i < source.count; i++) index[offset + i] = source.getX(i) + vertex;
        for (const row of (mesh.userData.objects as Record<string, number>[] | undefined) ?? [{ ...mesh.userData, start: 0, count: source.count }])
          rows.push({ ...row, start: (row.start as number) + offset });
        vertex += count; offset += source.count;
        yield;
      }
      geometry.setAttribute(SURFACE_LAYER, new THREE.BufferAttribute(layers, 1));
      geometry.setIndex(new THREE.BufferAttribute(index, 1));
      geometry.computeBoundingBox(); geometry.computeBoundingSphere();
      const mesh = new THREE.Mesh(geometry, arrays.surfaceMaterial(wet));
      mesh.name = wet ? 'surface-array' : 'surface-array-dry';
      mesh.userData = { layer: 'ground', objects: rows, surfaceLayers: sources.length };
      mesh.castShadow = sources[0].mesh.castShadow; mesh.receiveShadow = true;
      mesh.layers.mask = sources[0].mesh.layers.mask; mesh.renderOrder = sources[0].mesh.renderOrder;
      for (const { mesh: source } of sources) source.removeFromParent();
      parent.add(mesh);
      merged.push({ mesh, sources: sources.map(s => s.mesh), parent });
    }
  }
  return {
    steps: steps(),
    meshes: () => merged.map(m => m.mesh),
    restore() {
      for (const { mesh, sources, parent } of merged.splice(0)) {
        mesh.removeFromParent(); mesh.geometry.dispose();
        for (const source of sources) parent.add(source);
      }
    },
  };
}

/** Stored values (normalized integers stay raw), from plain or interleaved sources of the same array type. */
function copyRaw(from: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, to: THREE.BufferAttribute, at: number, count: number) {
  const size = from.itemSize, target = to.array as THREE.TypedArray;
  if (!(from as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute) {
    target.set((from.array as THREE.TypedArray).subarray(0, count * size), at * size);
    return;
  }
  const interleaved = from as THREE.InterleavedBufferAttribute, data = interleaved.data.array as THREE.TypedArray;
  for (let i = 0; i < count; i++) for (let c = 0; c < size; c++) target[(at + i) * size + c] = data[i * interleaved.data.stride + interleaved.offset + c];
}
