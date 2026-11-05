import * as T from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
import { sharedKtx } from '../../world/loadingKtx';
import { textureUploads } from '../../render/textureUploads';
import type { Corner, Hardpoints, SpringRest } from './linkage';

/** The prepared #19-style 4400 car (public/assets/vehicles/trophy4400, SPEC §2): one shared template per page.
 *  Every articulated part is a rig node (a bone) at its pivot; all meshes of one LOD are merged per material into
 *  rigid-skinned meshes (one bone per vertex, weight 1), so a truck costs one draw per material per LOD whatever its
 *  linkage count. Trucks are SkeletonUtils clones of the template: they share geometry, materials and textures; only
 *  the bone transforms are per truck. */

export const LOD_DISTANCES = [0, 35, 90] as const;
export interface Rest { p: T.Vector3; q: T.Quaternion; s: T.Vector3 }
export interface TrophyAsset {
  /** The model in the GLB ground frame (bones + one T.LOD of merged skinned meshes); clone with `instance()`. */
  template: T.Group;
  hardpoints: Hardpoints & { springs: Record<Corner, SpringRest> };
  /** Rest transforms of the rig nodes (local to their parent: the model root, or the upright for `wheel-*`). */
  rest: ReadonlyMap<string, Rest>;
  triangles: number[]; draws: number[];
}

let pending: Promise<TrophyAsset> | null = null;
let baseUrl = '/';
let gpu: T.WebGPURenderer | undefined;

/** Sets where and with what the asset loads; the first `trophyAsset()` after this uses it. */
export function configureTrophyAsset(base: string, renderer?: T.WebGPURenderer) { baseUrl = base; gpu = renderer ?? gpu; }

export function trophyAsset(): Promise<TrophyAsset> {
  pending ??= load(`${baseUrl}assets/vehicles/trophy4400/`).catch(error => { pending = null; throw error; });
  return pending;
}

/** A new truck model: a clone of the template with its own bones. */
export function instance(asset: TrophyAsset): T.Group { return cloneSkinned(asset.template) as T.Group; }

async function load(dir: string): Promise<TrophyAsset> {
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  if (gpu) loader.register(() => textureUploads(gpu!).plugin()).setKTX2Loader(sharedKtx(gpu));
  const [gltf, hardpoints] = await Promise.all([loader.loadAsync(`${dir}trophy4400.glb`),
    fetch(`${dir}hardpoints.json`).then(r => { if (!r.ok) throw new Error(`trophy4400 hardpoints: HTTP ${r.status}`); return r.json(); })]);
  const source = gltf.scene.getObjectByName('trophy4400');
  if (!source) throw new Error('trophy4400.glb: no trophy4400 root');
  return build(source, hardpoints);
}

/** Builds the skinned template from the raw glTF scene graph (exported for Node tests). */
export function build(source: T.Object3D, hardpoints: TrophyAsset['hardpoints']): TrophyAsset {
  source.position.set(0, 0, 0); source.quaternion.identity(); source.scale.set(1, 1, 1);
  source.updateMatrixWorld(true);
  const toModel = new T.Matrix4().copy(source.matrixWorld).invert();
  const template = new T.Group(); template.name = 'trophy4400';
  const rest = new Map<string, Rest>();
  const bones: T.Bone[] = [], boneOf = new Map<T.Object3D, number>();
  const addBone = (node: T.Object3D, parent: T.Object3D) => {
    const bone = new T.Bone(); bone.name = node.name;
    bone.position.copy(node.position); bone.quaternion.copy(node.quaternion); bone.scale.copy(node.scale);
    rest.set(node.name, { p: node.position.clone(), q: node.quaternion.clone(), s: node.scale.clone() });
    parent.add(bone); boneOf.set(node, bones.length); bones.push(bone);
    return bone;
  };
  // Rig nodes: the direct children of the root (except the hardpoint empties) and the wheels under the uprights.
  for (const node of source.children) {
    if (node.name === 'hardpoints') continue;
    const bone = addBone(node, template);
    for (const child of node.children) if (child.name.startsWith('wheel-')) addBone(child, bone);
  }
  template.updateMatrixWorld(true);
  const inverses = bones.map(b => new T.Matrix4().copy(b.matrixWorld).invert());

  // Group every mesh primitive by LOD and material, baked into the model frame with its owning bone index.
  const groups = [new Map<T.Material, T.BufferGeometry[]>(), new Map<T.Material, T.BufferGeometry[]>(), new Map<T.Material, T.BufferGeometry[]>()];
  const matrix = new T.Matrix4();
  source.traverse(object => {
    const mesh = object as T.Mesh;
    if (!mesh.isMesh) return;
    let lod = -1, owner: T.Object3D | null = mesh;
    for (let o: T.Object3D | null = mesh; o && lod < 0; o = o.parent) { const m = /_L(\d)$/.exec(o.name); if (m) lod = +m[1]; }
    while (owner && !boneOf.has(owner)) owner = owner.parent;
    if (lod < 0 || lod > 2 || !owner) return;
    matrix.multiplyMatrices(toModel, mesh.matrixWorld);
    const geometry = floatGeometry(mesh.geometry);
    geometry.applyMatrix4(matrix);
    if (matrix.determinant() < 0) flipWinding(geometry);
    const count = geometry.getAttribute('position').count, index = boneOf.get(owner)!;
    geometry.setAttribute('skinIndex', new T.Uint16BufferAttribute(new Uint16Array(count * 4).map((_, i) => i % 4 ? 0 : index), 4));
    geometry.setAttribute('skinWeight', new T.Float32BufferAttribute(new Float32Array(count * 4).map((_, i) => i % 4 ? 0 : 1), 4));
    const material = mesh.material as T.Material;
    (groups[lod].get(material) ?? groups[lod].set(material, []).get(material)!).push(geometry);
  });

  const skeleton = new T.Skeleton(bones, inverses);
  const lodNode = new T.LOD(); lodNode.name = 'trophy4400-lod';
  const triangles: number[] = [], draws: number[] = [];
  groups.forEach((byMaterial, lod) => {
    const level = new T.Group(); level.name = `trophy4400-L${lod}`;
    let tris = 0;
    for (const [material, parts] of byMaterial) {
      const geometry = mergeGeometries(parts, false);
      parts.forEach(p => p.dispose());
      if (!geometry) throw new Error(`trophy4400: cannot merge ${material.name} L${lod}`);
      tris += (geometry.index?.count ?? geometry.getAttribute('position').count) / 3;
      const skinned = new T.SkinnedMesh(geometry, material);
      skinned.name = `trophy4400-${material.name}-L${lod}`;
      skinned.castShadow = lod < 2; skinned.receiveShadow = true;
      // A fixed sphere over the truck and its travel; SkinnedMesh would otherwise skin every vertex on the CPU.
      skinned.boundingSphere = new T.Sphere(new T.Vector3(0, 1.0, 0), 3.3);
      skinned.boundingBox = new T.Box3(new T.Vector3(-2.6, -0.2, -1.4), new T.Vector3(2.6, 2.5, 1.4));
      skinned.bind(skeleton, new T.Matrix4());
      level.add(skinned);
    }
    triangles.push(tris); draws.push(level.children.length);
    lodNode.addLevel(level, LOD_DISTANCES[lod], lod ? 0.1 : 0);
  });
  template.add(lodNode);
  return { template, hardpoints, rest, triangles, draws };
}

/** A float, non-normalised copy with only the attributes every part shares (position, normal, uv). */
function floatGeometry(source: T.BufferGeometry): T.BufferGeometry {
  const out = new T.BufferGeometry();
  const count = source.getAttribute('position').count;
  for (const [name, size] of [['position', 3], ['normal', 3], ['uv', 2]] as const) {
    const a = source.getAttribute(name);
    const values = new Float32Array(count * size);
    if (a) for (let i = 0; i < count; i++) for (let k = 0; k < size; k++) values[i * size + k] = a.getComponent(i, k);
    out.setAttribute(name, new T.BufferAttribute(values, size));
  }
  if (!source.getAttribute('normal')) out.computeVertexNormals();
  const index = source.index;
  const indices = new Uint32Array(index ? index.count : count);
  for (let i = 0; i < indices.length; i++) indices[i] = index ? index.getX(i) : i;
  out.setIndex(new T.BufferAttribute(indices, 1));
  return out;
}

function flipWinding(geometry: T.BufferGeometry) {
  const index = geometry.index!;
  for (let i = 0; i < index.count; i += 3) { const b = index.getX(i + 1); index.setX(i + 1, index.getX(i + 2)); index.setX(i + 2, b); }
}

if (import.meta.hot) import.meta.hot.dispose(() => { pending = null; });
