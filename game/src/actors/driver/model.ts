import * as THREE from 'three/webgpu';
import { Discard, Fn, attribute, float, texture, uv, vec2, vec3, vec4 } from 'three/tsl';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';

/** A seated driver is one skinned draw (README "Cost": on the M1 Air every draw costs about 50 µs whatever its size):
 *  the character's skin, hoodie, hair, jeans and face and the steering wheel, merged into one geometry that samples one
 *  colour atlas, the wheel on a bone of its own. The index runs in detail tiers (body; wheel; legs; face), so a driver
 *  far from the camera draws a prefix of it (`tierEnds`, a per-driver drawRange). Teeth, tongue and shoes are never
 *  visible in a car and the cornea is a near-invisible gloss: left out. The normal maps (skin, jeans) are dropped. */

export const ATLAS = { width: 2048, height: 1024 } as const;
/** Gutter around each tile (px), filled by the source wrapping on: mip levels blur into it, not into a neighbour. */
const PAD = 4;
/** Detail tiers in index order: 0 always, 1 the drawn steering wheel, 2 the legs (mostly under the dash), 3 the face. */
export const TIERS = 4;
interface Tile { x: number; y: number; w: number; h: number; tier: number }
/** Atlas tiles (px) by source material name (player.glb) and the wheel. Skin 2048 -> 1024, the rest 1/2 to 1/4. */
const TILES: Record<string, Tile> = {
  Skin: { x: 0, y: 0, w: 1024, h: 1024, tier: 0 },
  Cloth_hoodie: { x: 1024, y: 0, w: 512, h: 512, tier: 0 },
  Hair_short02: { x: 1536, y: 0, w: 512, h: 512, tier: 0 },
  wheel: { x: 1920, y: 768, w: 128, h: 128, tier: 1 },
  Cloth_jeans: { x: 1024, y: 512, w: 512, h: 512, tier: 2 },
  Eye: { x: 1536, y: 512, w: 256, h: 256, tier: 3 },
  Eyebrow: { x: 1792, y: 512, w: 256, h: 256, tier: 3 },
  Eyelash: { x: 1536, y: 768, w: 256, h: 256, tier: 3 },
  Iris: { x: 1792, y: 768, w: 128, h: 128, tier: 3 },
};
/** The drawn steering wheel's plastic (linear colour) and roughness. */
const WHEEL = { color: new THREE.Color(0x1c1e21), roughness: 0.55 };
export const WHEEL_BONE = 'driver-wheel';
export const DRIVER_MESH = 'driver-body';

/** A plain steering wheel: rim, three spokes and a hub in the XY plane, facing +Z, radius 1 (the wheel bone scales it
 *  to the layout's rim radius). */
function wheelGeometry(): THREE.BufferGeometry {
  const tube = 0.016 / 0.185;
  const parts: THREE.BufferGeometry[] = [new THREE.TorusGeometry(1, tube, 8, 40)];
  for (const angle of [0, Math.PI, -Math.PI / 2]) {
    const spoke = new THREE.BoxGeometry(0.92, 0.16, 0.07);
    spoke.translate(0.5, 0, -0.04); spoke.rotateZ(angle);
    parts.push(spoke);
  }
  const hub = new THREE.CylinderGeometry(0.3, 0.34, 0.22, 16);
  hub.rotateX(Math.PI / 2); hub.translate(0, 0, -0.1);
  parts.push(hub);
  const merged = mergeGeometries(parts.map(p => p.toNonIndexed()))!;
  for (const p of parts) p.dispose();
  return merged;
}

interface Source { name: string; tile: Tile; map: THREE.Texture | null; color: THREE.Color; cutout: boolean }

export interface DriverModel {
  /** The character's bones, the wheel bone and the merged mesh: each driver is a SkeletonUtils clone of it. */
  root: THREE.Object3D;
  geometry: THREE.BufferGeometry;
  material: THREE.MeshStandardNodeMaterial;
  /** Index count at the end of each detail tier. */
  tierEnds: number[];
  /** What the atlas holds, tile by tile (bakeAtlas). */
  sources: Source[];
}

/** Builds the merged driver model from the loaded player character (actors/player.ts `template.scene`). */
export function driverModel(scene: THREE.Object3D): DriverModel {
  const root = cloneSkeleton(scene);
  root.position.set(0, 0, 0); root.quaternion.identity(); root.scale.set(1, 1, 1);
  root.updateMatrixWorld(true);
  const meshes: THREE.SkinnedMesh[] = [];
  root.traverse(o => { if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh); });
  if (!meshes.length) throw new Error('character has no skinned mesh');
  const { bones, boneInverses } = meshes[0].skeleton, bindMatrix = meshes[0].bindMatrix.clone();
  const parts: { name: string; mesh: THREE.SkinnedMesh | null; tile: Tile }[] = [];
  const sources: Source[] = [];
  for (const mesh of meshes) {
    const material = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as THREE.MeshStandardMaterial;
    mesh.removeFromParent();
    const tile = TILES[material.name];
    if (!tile) continue;
    if (mesh.skeleton.bones.some((b, i) => b !== bones[i]) || !mesh.bindMatrix.equals(bindMatrix))
      throw new Error(`character mesh ${mesh.name} is bound to another skeleton`);
    if (!mesh.matrixWorld.equals(root.matrixWorld)) throw new Error(`character mesh ${mesh.name} is not at the model origin`);
    parts.push({ name: material.name, mesh, tile });
    sources.push({ name: material.name, tile, map: material.map ?? null, color: material.color?.clone() ?? new THREE.Color(1, 1, 1),
      cutout: material.alphaTest > 0 || material.transparent });
  }
  parts.push({ name: 'wheel', mesh: null, tile: TILES.wheel });
  sources.push({ name: 'wheel', tile: TILES.wheel, map: null, color: WHEEL.color, cutout: false });
  parts.sort((a, b) => a.tile.tier - b.tile.tier);

  const wheelBone = new THREE.Bone(); wheelBone.name = WHEEL_BONE;
  root.add(wheelBone);
  const wheelIndex = bones.length;
  const wheel = wheelGeometry();
  const roughness = (mesh: THREE.SkinnedMesh | null) => mesh ? ((mesh.material as THREE.MeshStandardMaterial).roughness ?? 0.6) : WHEEL.roughness;

  let vertices = 0, indices = 0;
  const geometries = parts.map(p => p.mesh?.geometry ?? wheel);
  for (const g of geometries) { vertices += g.getAttribute('position').count; indices += g.index ? g.index.count : g.getAttribute('position').count; }
  const position = new Float32Array(vertices * 3), normal = new Float32Array(vertices * 3), uvs = new Float32Array(vertices * 2);
  const joints = new Uint8Array(vertices * 4), weights = new Float32Array(vertices * 4), rough = new Float32Array(vertices);
  if (wheelIndex > 255) throw new Error('driver skeleton exceeds 8-bit joint indices');
  const index = vertices > 65535 ? new Uint32Array(indices) : new Uint16Array(indices);
  const tierEnds = new Array<number>(TIERS).fill(0);
  let v0 = 0, i0 = 0;
  parts.forEach((part, k) => {
    const g = geometries[k], p = g.getAttribute('position'), n = g.getAttribute('normal'), t = g.getAttribute('uv');
    const j = g.getAttribute('skinIndex'), w = g.getAttribute('skinWeight'), r = roughness(part.mesh);
    // Atlas UVs: the tile's inner square (inside its gutter), in texture-sampling convention (the bake's uv()).
    const { x, y, w: tw, h: th } = part.tile;
    const u0 = (x + PAD) / ATLAS.width, vv0 = (y + PAD) / ATLAS.height, us = (tw - 2 * PAD) / ATLAS.width, vs = (th - 2 * PAD) / ATLAS.height;
    for (let i = 0; i < p.count; i++) {
      const o = v0 + i;
      position[o * 3] = p.getX(i); position[o * 3 + 1] = p.getY(i); position[o * 3 + 2] = p.getZ(i);
      normal[o * 3] = n.getX(i); normal[o * 3 + 1] = n.getY(i); normal[o * 3 + 2] = n.getZ(i);
      // The wheel samples the middle of its solid tile.
      uvs[o * 2] = u0 + (part.mesh ? t.getX(i) : 0.5) * us; uvs[o * 2 + 1] = vv0 + (part.mesh ? t.getY(i) : 0.5) * vs;
      if (part.mesh) for (let c = 0; c < 4; c++) { joints[o * 4 + c] = j.getComponent(i, c); weights[o * 4 + c] = w.getComponent(i, c); }
      else { joints[o * 4] = wheelIndex; weights[o * 4] = 1; }
      rough[o] = r;
    }
    const count = g.index ? g.index.count : p.count;
    for (let i = 0; i < count; i++) index[i0 + i] = v0 + (g.index ? g.index.getX(i) : i);
    v0 += p.count; i0 += count;
    tierEnds[part.tile.tier] = i0;
  });
  for (let t = 1; t < TIERS; t++) tierEnds[t] = Math.max(tierEnds[t], tierEnds[t - 1]);
  wheel.dispose();

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normal, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setAttribute('skinIndex', new THREE.BufferAttribute(joints, 4));
  geometry.setAttribute('skinWeight', new THREE.BufferAttribute(weights, 4));
  geometry.setAttribute('driverRoughness', new THREE.BufferAttribute(rough, 1));
  geometry.setIndex(new THREE.BufferAttribute(index, 1));

  // One material: the atlas (bakeAtlas) carries colour and the hair, brows and lashes' cutout alpha; roughness per vertex.
  const material = new THREE.MeshStandardNodeMaterial({ side: THREE.DoubleSide, alphaTest: 0.5, metalness: 0 });
  material.name = 'driver';
  material.roughnessNode = attribute('driverRoughness', 'float');
  const mesh = new THREE.SkinnedMesh(geometry, material);
  mesh.name = DRIVER_MESH;
  // Bone matrices: the character's, then the wheel bone, whose inverse undoes the bind so its vertices sit in its frame.
  mesh.bind(new THREE.Skeleton([...bones, wheelBone], [...boneInverses, bindMatrix.clone().invert()]), bindMatrix);
  root.add(mesh);
  return { root, geometry, material, tierEnds, sources };
}

/** Bakes the atlas on the GPU once: each tile is a fullscreen quad that keeps only its own rectangle and samples its
 *  source map (times the material colour) there, the gutter wrapping on. sRGB 8-bit with mipmaps (about 11 MB). */
export function bakeAtlas(renderer: THREE.WebGPURenderer, sources: readonly Source[]): THREE.Texture {
  const target = new THREE.RenderTarget(ATLAS.width, ATLAS.height, { type: THREE.UnsignedByteType, depthBuffer: false, generateMipmaps: true,
    minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter, colorSpace: THREE.SRGBColorSpace });
  target.texture.name = 'driver-atlas';
  target.texture.colorSpace = THREE.SRGBColorSpace;
  target.texture.generateMipmaps = true;
  target.texture.anisotropy = 4;
  const previous = renderer.getRenderTarget(), autoClear = renderer.autoClear;
  const clear = renderer.getClearColor(new THREE.Color()), alpha = renderer.getClearAlpha();
  let quad: THREE.QuadMesh | null = null;
  const materials: THREE.Material[] = [];
  try {
    renderer.setRenderTarget(target);
    renderer.setClearColor(0x000000, 0); renderer.clear();
    renderer.autoClear = false;
    for (const s of sources) {
      const { x, y, w, h } = s.tile;
      const lo = vec2(x / ATLAS.width, y / ATLAS.height), hi = vec2((x + w) / ATLAS.width, (y + h) / ATLAS.height);
      const origin = vec2((x + PAD) / ATLAS.width, (y + PAD) / ATLAS.height);
      const size = vec2((w - 2 * PAD) / ATLAS.width, (h - 2 * PAD) / ATLAS.height);
      const material = new THREE.MeshBasicNodeMaterial({ depthTest: false, depthWrite: false });
      material.fragmentNode = Fn(() => {
        const at = uv();
        const sample = (s.map ? texture(s.map, at.sub(origin).div(size)) : vec4(1, 1, 1, 1)).toVar();
        Discard(at.x.lessThan(lo.x).or(at.y.lessThan(lo.y)).or(at.x.greaterThanEqual(hi.x)).or(at.y.greaterThanEqual(hi.y)));
        return vec4(sample.rgb.mul(vec3(s.color.r, s.color.g, s.color.b)), s.cutout ? sample.a : float(1));
      })();
      materials.push(material);
      if (quad) quad.material = material; else quad = new THREE.QuadMesh(material);
      quad.render(renderer);
    }
  } finally {
    renderer.setRenderTarget(previous); renderer.autoClear = autoClear; renderer.setClearColor(clear, alpha);
    for (const m of materials) m.dispose();
  }
  return target.texture;
}
