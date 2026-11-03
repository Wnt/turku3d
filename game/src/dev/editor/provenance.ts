// What the tiles carry about who placed an editable, plus the generator stage and rules a consumer should look at
// (EDIT-FORMAT.md "Provenance"). Static stage hints name pipeline/ files; nothing here reads the pipeline.
import type * as THREE from 'three/webgpu';
import type { Editable } from './catalog';

/** Source dataset of a placement id prefix (pipeline/turku3d/props/__init__.py SOURCES, coarse/tree_inputs.py…). */
export function datasetOf(objectId: string): { dataset: string; feature: string | null } {
  const i = objectId.indexOf(':');
  return i < 0 ? { dataset: objectId, feature: null } : { dataset: objectId.slice(0, i), feature: objectId.slice(i + 1) };
}

const PROP_STAGE = {
  stage: 'furniture', code: ['pipeline/turku3d/coarse/furniture.py', 'pipeline/turku3d/props/__init__.py', 'pipeline/turku3d/props/orientation.py'],
  rules: ['props.read_sources: register first, OSM fallback (OSM_DUPLICATE 3 m), lamps by the lit-street spacing rule only where neither has one',
    'props.off_carriageway: nothing but signs on a carriageway (KERB_MOVE)', 'props.face_road / orientation: yaw toward the nearest road',
    'furniture.produce: BUILDING_MOVE 1.0 m (lamps LAMP_MOVE 1.5 m) out of footprints, WATER_MOVE 1.5 m out of water, CLEARANCE 0.3 m, dropped on decks'],
};
const TREE_STAGES: Record<string, { stage: string; code: string[] }> = {
  puut: { stage: 'trees (register)', code: ['pipeline/turku3d/coarse/tree_inputs.py'] },
  osm: { stage: 'trees (OSM natural=tree)', code: ['pipeline/turku3d/coarse/tree_inputs.py'] },
  lidar: { stage: 'trees (LiDAR canopy detection, tile-local x:z in the id)', code: ['pipeline/turku3d/coarse/canopy_trees.py', 'pipeline/turku3d/vegetation/trees.py'] },
  area: { stage: 'trees (area fill, `area:<area>:<x>:<z>`)', code: ['pipeline/turku3d/coarse/forests.py'] },
};
const LAMP_STAGE = {
  stage: 'furniture lights', code: ['pipeline/turku3d/coarse/furniture.py', 'pipeline/turku3d/props/lights.py', 'pipeline/turku3d/night_lighting.py'],
  rules: ['register lamps (LAMP_LAYERS, LAMP_DUPLICATE 1.5 m), OSM street_lamp, lit-street spacing rule', 'lights.post_light: post yaw toward the street edge'],
  missing: 'post records (manifest `lights`, render/lights/tileLights.ts) carry no register/OSM id: match by tile + post base within 0.15 m',
};

/** The vegetation owner node (EXT_mesh_gpu_instancing prototype) of a tree id in the resident tile models. */
function treeOwner(models: Iterable<THREE.Object3D>, id: string): { data: Record<string, unknown>; index: number } | null {
  for (const model of models) {
    let found: { data: Record<string, unknown>; index: number } | null = null;
    model.traverse(node => {
      if (found || node.userData.layer !== 'vegetation') return;
      const ids = node.userData.instance_object_ids as unknown[] | undefined;
      const index = Array.isArray(ids) ? ids.indexOf(id) : -1;
      if (index >= 0) found = { data: node.userData, index };
    });
    if (found) return found;
  }
  return null;
}

const ROW_FIELDS = ['prop_type', 'sign_code', 'material_key', 'pole_id', 'pole_base', 'pole_height', 'texture_uv', 'kind', 'variant'];

export function provenanceOf(e: Editable, models: Iterable<THREE.Object3D>): Record<string, unknown> {
  const { dataset, feature } = datasetOf(e.objectId);
  if (e.ref.kind === 'prop') {
    const row = e.ref.row, mesh = e.ref.mesh;
    return { dataset, feature, generator: PROP_STAGE,
      record: Object.fromEntries(ROW_FIELDS.filter(k => row[k] !== undefined && row[k] !== '').map(k => [k, row[k]])),
      batch: { object_id: mesh.userData.object_id ?? null, material_key: mesh.userData.material_key ?? null, row: e.ref.rowIndex, start: e.ref.start, count: e.ref.count },
      yaw: e.yawSource === 'estimate-axis' ? 'estimated from the row geometry (long axis, mod 180°): tiles carry no prop yaw' : 'unknown',
      missing: ['prop yaw', 'orientation rule result', 'register attributes used for variant'] };
  }
  if (e.ref.kind === 'tree') {
    const owner = treeOwner(models, e.objectId);
    const species = owner ? (owner.data.instance_species as unknown[] | undefined)?.[owner.index] ?? null : null;
    return { dataset, feature, generator: TREE_STAGES[dataset] ?? { stage: 'trees', code: ['pipeline/turku3d/coarse/tree_inputs.py'] },
      record: { variant: owner?.data.variant ?? null, species, instance: owner?.index ?? null, asset: e.ref.asset,
        near_water: owner ? (owner.data.instance_near_water as unknown[] | undefined)?.[owner.index] ?? null : null,
        prototype: owner?.data.object_id ?? null },
      missing: ['register attributes (trunk diameter, height class) behind the instance scale'] };
  }
  const lamp = e.ref.lamp;
  return { dataset: 'lights', feature: null, generator: LAMP_STAGE,
    record: { post_index: e.ref.index, lamp_key: lamp.key, scale: lamp.scale, kind: lamp.kind,
      light_head: { x: lamp.light.x, y: lamp.light.y, z: lamp.light.z } },
    missing: ['post register/OSM id', 'post style'] };
}
