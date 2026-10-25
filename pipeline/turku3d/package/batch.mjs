// Batch compatible materials without losing the source-object ranges needed by QA.
export async function batchVisual(doc, scene, tile, { dedup, joinPrimitives, transformPrimitive }) {
  await doc.transform(dedup({ propertyTypes: ['Texture', 'Material'] }));
  const groups = new Map();
  const materials = doc.getRoot().listMaterials();
  const [, level, i, j] = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(tile);
  const origin = [Number(i) * 250 * 2 ** Number(level), 0, Number(j) * 250 * 2 ** Number(level)];
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const matrix = node.getWorldMatrix();
    matrix[12] -= origin[0]; matrix[14] -= origin[2];
    for (const prim of mesh.listPrimitives()) {
      // Source accessors may be shared. Each baked primitive owns its mutable attributes.
      for (const semantic of prim.listSemantics()) prim.setAttribute(semantic, prim.getAttribute(semantic).clone());
      if (prim.getIndices()) prim.setIndices(prim.getIndices().clone());
      transformPrimitive(prim, matrix);
      const e = node.getExtras();
      const key = JSON.stringify([materials.indexOf(prim.getMaterial()), prim.listSemantics().sort(), e.layer]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ prim, extras: e });
    }
    node.setMesh(null);
  }
  for (const entries of groups.values()) {
    const prim = joinPrimitives(entries.map(e => e.prim));
    let start = 0;
    const objects = entries.map(e => {
      const count = e.prim.getIndices()?.getCount() ?? e.prim.getAttribute('POSITION').getCount();
      const range = { start, count, ...e.extras }; start += count; return range;
    });
    const first = entries[0].extras;
    const extras = entries.length === 1 ? first : {
      layer: first.layer, kind: first.kind, tile, material_key: first.material_key,
      object_id: `${tile}/batch/${groups.size}/${scene.listChildren().length}`, objects,
    };
    scene.addChild(doc.createNode().setTranslation(origin).setExtras(extras)
      .setMesh(doc.createMesh().addPrimitive(prim)));
  }
}
