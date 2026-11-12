import type { PreparedMaterial, PreparedMesh, PreparedScene } from './preparedScene';

/** An appearance-only change to one prepared scene. The same document is applied live in a running session
 *  (`window.__dev.scene.patch`) and published by `python -m turku3d.scene.appearance` (pipeline/turku3d/scene). */
export interface AppearancePatch {
  schema: 'turku-scene-appearance/1';
  /** Scene id the patch was written for; a patch never applies to another scene. */
  scene: string;
  /** Complete replacement records for existing material ids. `uvScale` changes mesh UVs, so only the pipeline
   *  publish applies it (it knows the scale the recipe compiled); a live patch with `uvScale` is refused. */
  materials?: { material: PreparedMaterial; uvScale?: [number, number] }[];
  /** A different immutable model for an existing entity's asset. */
  assets?: { entityId: string; url: string; sha256: string; bounds?: unknown }[];
}

const object = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
const fail = (why: string): never => { throw new Error(`Invalid appearance patch: ${why}`); };

export function readAppearancePatch(value: unknown): AppearancePatch {
  if (!object(value) || value.schema !== 'turku-scene-appearance/1' || typeof value.scene !== 'string' || !value.scene) fail('schema or scene');
  const patch = value as Record<string, any>;
  for (const key of Object.keys(patch)) if (!['schema', 'scene', 'materials', 'assets'].includes(key)) fail(`unknown field ${key}`);
  if (patch.materials !== undefined && (!Array.isArray(patch.materials)
    || !patch.materials.every((row: unknown) => object(row) && object(row.material) && typeof row.material.id === 'string'))) fail('materials');
  if (patch.assets !== undefined && (!Array.isArray(patch.assets) || !patch.assets.every((row: unknown) => object(row)
    && typeof row.entityId === 'string' && typeof row.url === 'string' && /^[a-f0-9]{64}$/.test(row.sha256)))) fail('assets');
  return patch as AppearancePatch;
}

/** The patched scene document for a live experiment. Unchanged meshes keep their identity (no copies of the
 *  geometry arrays); the result still goes through readPreparedScene and appearanceChanges before it is drawn. */
export function patchScene(scene: PreparedScene, patch: AppearancePatch): PreparedScene {
  if (patch.scene !== scene.id) throw new Error(`Appearance patch for ${patch.scene} cannot change ${scene.id}`);
  const materials = new Map(scene.materials.map(m => [m.id, m]));
  for (const row of patch.materials ?? []) {
    if (!materials.has(row.material.id)) throw new Error(`Appearance patch names an unknown material: ${row.material.id}`);
    if (row.uvScale !== undefined) throw new Error(`uvScale changes mesh UVs: publish ${row.material.id} with python -m turku3d.scene.appearance`);
    materials.set(row.material.id, row.material);
  }
  const assets = scene.assets.map(asset => ({ ...asset }));
  for (const row of patch.assets ?? []) {
    const asset = assets.find(a => a.entityId === row.entityId);
    if (!asset) throw new Error(`Appearance patch names an unknown asset entity: ${row.entityId}`);
    Object.assign(asset, { url: row.url, sha256: row.sha256 });
  }
  return { ...scene, release: `patched:${scene.release.replace(/^patched:/, '')}`, materials: [...materials.values()], assets };
}

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);
function sameNumbers(a: readonly number[] | undefined, b: readonly number[] | undefined) {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
const ARRAYS = ['positions', 'indices', 'normals', 'bakedVisibility', 'nightIrradiance'] as const;
function sameMeshExceptUv(a: PreparedMesh, b: PreparedMesh) {
  if (a === b) return true;
  const { positions: _p, indices: _i, normals: _n, bakedVisibility: _b, nightIrradiance: _r, uv: _u, ...restA } = a;
  const { positions: _P, indices: _I, normals: _N, bakedVisibility: _B, nightIrradiance: _R, uv: _U, ...restB } = b;
  return same(restA, restB) && ARRAYS.every(key => sameNumbers(a[key], b[key]));
}

/** Appearance-only difference between two documents of one scene. Throws when anything gameplay or lighting
 *  reads differs (geometry, collision, supports, fixtures, emitters, occluders, coverage, spawn): such a change
 *  needs a full scene load, not an appearance update. */
export function appearanceChanges(current: PreparedScene, next: PreparedScene) {
  for (const key of ['schema', 'id', 'origin', 'bounds', 'boundaries', 'supports', 'fixtures', 'emitters', 'lightOccluders',
    'coverage', 'collision', 'spawn'] as const) {
    if (!same(current[key], next[key])) throw new Error(`Not an appearance change: ${key} differs (reload the scene)`);
  }
  if (current.meshes.length !== next.meshes.length) throw new Error('Not an appearance change: mesh count differs (reload the scene)');
  let uvMeshes = 0;
  for (let i = 0; i < current.meshes.length; i++) {
    const a = current.meshes[i], b = next.meshes[i];
    if (!sameMeshExceptUv(a, b)) throw new Error(`Not an appearance change: mesh ${b.id} differs (reload the scene)`);
    if (a !== b && !sameNumbers(a.uv, b.uv)) uvMeshes++;
  }
  const before = new Map(current.materials.map(m => [m.id, m]));
  if (current.materials.length !== next.materials.length || next.materials.some(m => !before.has(m.id)))
    throw new Error('Not an appearance change: material ids differ (reload the scene)');
  const materials = next.materials.filter(m => !same(before.get(m.id), m)).map(m => m.id);
  const oldAssets = new Map(current.assets.map(a => [a.id, a]));
  const assets = next.assets.filter(a => !same(oldAssets.get(a.id), a)).map(a => a.entityId);
  for (const id of oldAssets.keys()) if (!next.assets.some(a => a.id === id)) assets.push(oldAssets.get(id)!.entityId);
  return { materials, assets, uvMeshes };
}
