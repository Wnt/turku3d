import * as THREE from 'three/webgpu';
import type { ManifestEntry, Manifest } from './manifest';

export type Vec3 = [number, number, number];
export interface PreparedTexture { url: string; sha256: string; wrap: 'repeat' | 'clamp' }
export interface PreparedMaterial {
  id: string; color: Vec3; roughness: number; metalness: number;
  water?: { point: Vec3; normal: Vec3; band: string };
  baseColorTexture?: PreparedTexture; normalTexture?: PreparedTexture; ormTexture?: PreparedTexture;
}
export interface PreparedMesh {
  id: string; entityId: string; materialId: string; positions: number[]; indices: number[];
  render?: boolean; normals?: number[]; uv?: number[]; collision: 'solid' | 'passable' | 'sensor' | 'dynamic'; supportId: string | null;
}
/** Current compiled scene. Positions and normals already use engine axes; no gameplay semantics are recovered from triangles. */
export interface PreparedScene {
  schema: 'turku-scene/1'; release: string; id: string; origin: Vec3; bounds: [number, number, number, number];
  sources: unknown[];
  materials: PreparedMaterial[];
  boundaries: { id: string; positions: Vec3[] }[];
  supports: { id: string; kind: 'road' | 'footway' | 'quay' | 'ground'; boundaryIds: string[]; meshId: string }[];
  meshes: PreparedMesh[];
  assets: { id: string; entityId: string; url: string; sha256: string; position: Vec3; recipe: 'building' | 'vegetation' | 'context' }[];
  fixtures: { id: string; family: string; supportId: string; position: Vec3; yaw: number; dimensions: Vec3; meshIds: string[]; emitterIds: string[] }[];
  emitters: { id: string; fixtureId: string; position: Vec3; color: Vec3; intensity: number; range: number }[];
  coverage: { mode: 'replace'; bounds: [number, number, number, number]; entityIds: string[] };
  collision: Record<string, ManifestEntry>;
  spawn: NonNullable<Manifest['spawn']>;
}

const object = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const vector = (v: unknown, n = 3): v is number[] => Array.isArray(v) && v.length === n && v.every(finite);
const name = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const unit = (v: unknown) => finite(v) && v >= 0 && v <= 1;
const bounds = (v: unknown) => vector(v, 4) && v[0] < v[2] && v[1] < v[3];
const fail = (why: string): never => { throw new Error(`Invalid prepared scene: ${why}`); };

/** Validate all references before allocating GPU buffers or publishing any collision. */
export function readPreparedScene(value: unknown): PreparedScene {
  if (!object(value) || value.schema !== 'turku-scene/1') fail('schema');
  const s = value as Record<string, any>;
  if (!name(s.release) || !name(s.id) || !vector(s.origin) || !bounds(s.bounds)) fail('identity or bounds');
  for (const key of ['sources', 'materials', 'boundaries', 'supports', 'meshes', 'assets', 'fixtures', 'emitters'])
    if (!Array.isArray(s[key])) fail(`${key} must be present`);
  const ids = (key: string) => {
    const out = new Set<string>();
    for (const row of s[key]) { if (!object(row) || !name(row.id) || out.has(row.id)) fail(`${key} id`); out.add(row.id); }
    return out;
  };
  const materials = ids('materials'), boundaries = ids('boundaries'), supports = ids('supports');
  const meshes = ids('meshes'); ids('assets');
  const fixtures = ids('fixtures'), emitters = ids('emitters');
  const refs = (v: unknown, known: Set<string>) => Array.isArray(v) && v.every(id => typeof id === 'string' && known.has(id));
  for (const m of s.materials) {
    if (!vector(m.color) || !m.color.every(unit) || !unit(m.roughness) || !unit(m.metalness)) fail(`material ${m.id}`);
    if (m.water !== undefined && (!object(m.water) || !vector(m.water.point) || !vector(m.water.normal)
      || Math.abs(Math.hypot(...m.water.normal) - 1) > .001 || !name(m.water.band))) fail(`water ${m.id}`);
    for (const key of ['baseColorTexture', 'normalTexture', 'ormTexture']) {
      const t = m[key];
      if (t !== undefined && (!object(t) || !name(t.url) || !/^[a-f0-9]{64}$/.test(t.sha256) || !['repeat', 'clamp'].includes(t.wrap))) fail(`texture ${m.id}/${key}`);
    }
  }
  for (const b of s.boundaries) if (!Array.isArray(b.positions) || b.positions.length < 2 || !b.positions.every((p: unknown) => vector(p))) fail(`boundary ${b.id}`);
  for (const p of s.supports) if (!['road', 'footway', 'quay', 'ground'].includes(p.kind) || !refs(p.boundaryIds, boundaries) || !meshes.has(p.meshId)) fail(`support ${p.id}`);
  const entities = new Set<string>();
  for (const m of s.meshes) {
    if (!name(m.entityId) || !materials.has(m.materialId) || !Array.isArray(m.positions) || m.positions.length < 9
      || m.positions.length % 3 || !m.positions.every(finite) || !Array.isArray(m.indices) || m.indices.length < 3 || m.indices.length % 3
      || !m.indices.every((i: unknown) => finite(i) && Number.isInteger(i) && i >= 0 && i < m.positions.length / 3)
      || !['solid', 'passable', 'sensor', 'dynamic'].includes(m.collision) || !(m.supportId === null || supports.has(m.supportId))) fail(`mesh ${m.id}`);
    if (m.render !== undefined && typeof m.render !== 'boolean') fail(`render ${m.id}`);
    if (m.normals !== undefined && (!vector(m.normals, m.positions.length))) fail(`normals ${m.id}`);
    if (m.uv !== undefined && !vector(m.uv, m.positions.length / 3 * 2)) fail(`uv ${m.id}`);
    entities.add(m.entityId);
  }
  for (const a of s.assets) {
    if (!name(a.entityId) || !name(a.url) || !/^[a-f0-9]{64}$/.test(a.sha256) || !vector(a.position) || !['building', 'vegetation', 'context'].includes(a.recipe)) fail(`asset ${a.id}`);
    entities.add(a.entityId);
  }
  for (const f of s.fixtures) if (!name(f.family) || !supports.has(f.supportId) || !vector(f.position) || !finite(f.yaw)
    || !vector(f.dimensions) || !f.dimensions.every((n: number) => n > 0) || !refs(f.meshIds, meshes) || !refs(f.emitterIds, emitters)) fail(`fixture ${f.id}`);
  for (const e of s.emitters) if (!fixtures.has(e.fixtureId) || !vector(e.position) || !vector(e.color) || !e.color.every(unit)
    || !finite(e.intensity) || e.intensity < 0 || !finite(e.range) || e.range <= 0) fail(`emitter ${e.id}`);
  if (!object(s.coverage) || s.coverage.mode !== 'replace' || !bounds(s.coverage.bounds) || !refs(s.coverage.entityIds, entities)) fail('coverage');
  if (!object(s.collision) || !Object.keys(s.collision).length) fail('collision');
  for (const [id, entry] of Object.entries(s.collision)) {
    if (!/^c_-?\d+_-?\d+$/.test(id) || !object(entry) || !name(entry.url) || !/^[a-f0-9]{64}$/.test(entry.sha256)
      || !Number.isInteger(entry.bytes) || entry.bytes <= 0 || !Number.isInteger(entry.tris) || entry.tris < 0 || !vector(entry.h, 2)) fail(`collision ${id}`);
  }
  const p = s.spawn;
  if (!object(p) || !vector(p.position) || !finite(p.headingDeg) || !object(p.vehicle) || !vector(p.vehicle.position) || !finite(p.vehicle.yaw)) fail('spawn');
  for (const pos of [p.position, p.vehicle.position]) if (pos[0] < s.bounds[0] || pos[0] >= s.bounds[2] || pos[2] < s.bounds[1] || pos[2] >= s.bounds[3]) fail('spawn outside bounds');
  return s as PreparedScene;
}

/** Shared material batches retain explicit entity ranges for picking; fixtures never duplicate their compiled meshes. */
export function buildPreparedModel(scene: PreparedScene, textures: ReadonlyMap<string, THREE.Texture> = new Map()): THREE.Group {
  const root = new THREE.Group(); root.name = `scene:${scene.id}`;
  root.userData.preparedScene = { id: scene.id, release: scene.release, bounds: scene.bounds, fixtures: scene.fixtures };
  for (const material of scene.materials) {
    const parts = scene.meshes.filter(mesh => mesh.render !== false && mesh.materialId === material.id);
    if (!parts.length) continue;
    const vertices = parts.reduce((n, m) => n + m.positions.length / 3, 0), triangles = parts.reduce((n, m) => n + m.indices.length, 0);
    const positions = new Float32Array(vertices * 3), normals = new Float32Array(vertices * 3), uv = new Float32Array(vertices * 2), indices = new Uint32Array(triangles);
    let vertex = 0, index = 0;
    const ranges: { id: string; entityId: string; start: number; count: number }[] = [];
    for (const part of parts) {
      positions.set(part.positions, vertex * 3);
      if (part.normals) normals.set(part.normals, vertex * 3);
      if (part.uv) uv.set(part.uv, vertex * 2);
      for (const i of part.indices) indices[index++] = vertex + i;
      ranges.push({ id: part.id, entityId: part.entityId, start: index - part.indices.length, count: part.indices.length });
      vertex += part.positions.length / 3;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2)); geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    if (parts.every(p => p.normals)) geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3)); else geometry.computeVertexNormals();
    geometry.computeBoundingBox(); geometry.computeBoundingSphere();
    const drawMaterial = new THREE.MeshStandardMaterial({ color: new THREE.Color().setRGB(...material.color, THREE.LinearSRGBColorSpace), roughness: material.roughness, metalness: material.metalness });
    if (material.baseColorTexture) drawMaterial.map = textures.get(textureKey(material.baseColorTexture, true)) ?? null;
    if (material.normalTexture) drawMaterial.normalMap = textures.get(textureKey(material.normalTexture, false)) ?? null;
    if (material.ormTexture) {
      const orm = textures.get(textureKey(material.ormTexture, false)) ?? null;
      drawMaterial.aoMap = drawMaterial.roughnessMap = drawMaterial.metalnessMap = orm;
    }
    drawMaterial.name = material.id;
    const mesh = new THREE.Mesh(geometry, drawMaterial); mesh.name = `prepared:${material.id}`;
    mesh.castShadow = mesh.receiveShadow = true; mesh.userData.preparedEntities = ranges;
    if (material.water) {
      mesh.userData.material_key = 'water'; mesh.userData.water_band = material.water.band;
      mesh.userData.waterSurface = { point: material.water.point, normal: material.water.normal };
      mesh.castShadow = false;
    }
    mesh.matrixAutoUpdate = false; root.add(mesh);
  }
  return root;
}

/** Texture coordinates arrive in glTF convention (top-left image origin), with physical scale already compiled. */
export const textureKey = (t: PreparedTexture, color: boolean) => `${t.sha256}/${t.wrap}/${color ? 'srgb' : 'linear'}`;

export function closePreparedImages(textures: Iterable<THREE.Texture>) {
  const images = new Set<ImageBitmap>();
  for (const texture of textures) if ((texture.image as ImageBitmap | undefined)?.close) images.add(texture.image as ImageBitmap);
  for (const image of images) image.close();
}
export function disposePreparedTextures(textures: Iterable<THREE.Texture>) {
  const unique = new Set(textures);
  for (const texture of unique) texture.dispose();
  closePreparedImages(unique);
}

/** Decode bounded batches before publication; albedo is sRGB, normal/ORM channels remain linear. */
export async function loadPreparedTextures(scene: PreparedScene, base: string, fetcher: typeof fetch = fetch,
  decode: (blob: Blob) => Promise<ImageBitmap> = blob => createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }),
  decodeCompressed?: (bytes: ArrayBuffer) => Promise<THREE.Texture>) {
  const wanted = new Map<string, { descriptor: PreparedTexture; color: boolean }>();
  for (const m of scene.materials) for (const [descriptor, color] of [[m.baseColorTexture, true], [m.normalTexture, false], [m.ormTexture, false]] as const)
    if (descriptor) wanted.set(textureKey(descriptor, color), { descriptor, color });
  const out = new Map<string, THREE.Texture>(), jobs = [...wanted];
  try {
    for (let i = 0; i < jobs.length; i += 4) {
      const results = await Promise.allSettled(jobs.slice(i, i + 4).map(async ([key, { descriptor, color }]) => {
        const response = await fetcher(new URL(descriptor.url, base), { signal: AbortSignal.timeout(15000) });
        if (!response.ok) throw new Error(`Prepared texture: HTTP ${response.status} (${descriptor.url})`);
        const bytes = await response.arrayBuffer();
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
        if (digest !== descriptor.sha256) throw new Error(`Prepared texture: hash mismatch (${descriptor.url})`);
        const compressed = /\.ktx2(?:[?#]|$)/i.test(descriptor.url);
        if (compressed && !decodeCompressed) throw new Error(`Prepared texture: KTX2 decoder unavailable (${descriptor.url})`);
        const texture = compressed ? await decodeCompressed!(bytes) : new THREE.Texture(await decode(new Blob([bytes])));
        texture.name = descriptor.url; texture.flipY = false;
        texture.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        texture.wrapS = texture.wrapT = descriptor.wrap === 'repeat' ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
        texture.anisotropy = 8; texture.needsUpdate = true; out.set(key, texture);
      }));
      const error = results.find(result => result.status === 'rejected');
      if (error?.status === 'rejected') throw error.reason;
    }
    return out;
  } catch (error) { disposePreparedTextures(out.values()); throw error; }
}
