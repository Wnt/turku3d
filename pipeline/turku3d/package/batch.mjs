// Batch compatible materials without losing the source-object ranges needed by QA.
import { MAX_BATCH_INDICES, primitiveChunks } from './primitive_chunks.mjs';

export async function batchVisual(doc, scene, tile, { dedup, joinPrimitives, transformPrimitive, compactPrimitive }) {
  await doc.transform(dedup({ propertyTypes: ['Texture', 'Material'] }));
  const groups = new Map();
  const materials = doc.getRoot().listMaterials();
  const [, level, i, j] = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(tile);
  const origin = [Number(i) * 250 * 2 ** Number(level), 0, Number(j) * 250 * 2 ** Number(level)];
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    // Instanced meshes already batch their repeated geometry; preserve their transforms.
    if (!mesh || node.getExtension('EXT_mesh_gpu_instancing')) continue;
    const matrix = node.getWorldMatrix();
    matrix[12] -= origin[0]; matrix[14] -= origin[2];
    for (const source of mesh.listPrimitives()) {
      const prim = source.clone();
      // Source accessors may be shared. Each baked primitive owns its mutable attributes.
      for (const semantic of prim.listSemantics()) prim.setAttribute(semantic, prim.getAttribute(semantic).clone());
      if (prim.getIndices()) prim.setIndices(prim.getIndices().clone());
      transformPrimitive(prim, matrix);
      const e = { ...node.getExtras() };
      // bindProgram has compiled these source rows into the _KIT_* attributes.
      // Keeping them here repeats megabytes of build-only instructions in GLB JSON.
      if (prim.getAttribute('_KIT_EXTENT')) delete e.facade_kit_walls;
      const key = JSON.stringify([materials.indexOf(prim.getMaterial()), prim.listSemantics().sort(), e.layer, e.reflection_surface]);
      if (!groups.has(key)) groups.set(key, []);
      for (const part of primitiveChunks(prim, compactPrimitive)) {
        groups.get(key).push({ prim:part, extras:e });
      }
    }
    node.setMesh(null);
  }
  const batches = [];
  for (const group of groups.values()) {
    let entries = [], count = 0;
    for (const entry of group) {
      const size = entry.prim.getIndices()?.getCount() ?? entry.prim.getAttribute('POSITION').getCount();
      if (entries.length && count + size > MAX_BATCH_INDICES) {
        batches.push(entries); entries = []; count = 0;
      }
      entries.push(entry); count += size;
    }
    if (entries.length) batches.push(entries);
  }
  for (const entries of batches) {
    const prim = joinPrimitives(entries.map(e => e.prim));
    let start = 0;
    const objects = entries.map(e => {
      const count = e.prim.getIndices()?.getCount() ?? e.prim.getAttribute('POSITION').getCount();
      const range = { start, count, ...e.extras }; start += count; return range;
    });
    const first = entries[0].extras;
    const extras = entries.length === 1 ? first : {
      layer: first.layer, kind: first.kind, tile, material_key: first.material_key,
      reflection_surface: first.reflection_surface,
      object_id: `${tile}/batch/${groups.size}/${scene.listChildren().length}`, objects,
    };
    scene.addChild(doc.createNode().setTranslation(origin).setExtras(extras)
      .setMesh(doc.createMesh().addPrimitive(prim)));
  }
}
