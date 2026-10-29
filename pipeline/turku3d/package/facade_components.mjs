// Validate expanded source boxes first; instance only intact canonical boxes after assembly.
export function instanceFacadeComponents(doc, scene, tile, EXTMeshGPUInstancing) {
  const groups = new Map();
  const buffer = doc.getRoot().listBuffers()[0];
  const materials = doc.getRoot().listMaterials();
  const acc = (type, values) => doc.createAccessor().setType(type)
    .setArray(new Float32Array(values)).setBuffer(buffer);
  for (const node of doc.getRoot().listNodes()) {
    const box = node.getExtras().component_box;
    if (!box || !node.getMesh()) continue;
    const prim = node.getMesh().listPrimitives()[0];
    const position = prim.getAttribute('POSITION');
    if (position.getCount() !== 24 || prim.getIndices()?.getCount() !== 36) continue;
    const [tx,,tz] = box.tangent;
    const local = [], normals = [];
    const sourceNormals = prim.getAttribute('NORMAL');
    for (let i=0; i<24; i++) {
      const p = position.getElement(i, []).map((v,k) => v-box.center[k]);
      local.push((p[0]*tx+p[2]*tz)/box.size[0], p[1]/box.size[1], (-p[0]*tz+p[2]*tx)/box.size[2]);
      const n = sourceNormals.getElement(i, []);
      normals.push(n[0]*tx+n[2]*tz, n[1], -n[0]*tz+n[2]*tx);
    }
    // A wall assembler cut must never be undone by restoring its box prototype.
    if (local.some(v => Math.abs(Math.abs(v)-.5) > .003)) continue;
    const key = materials.indexOf(prim.getMaterial());
    if (!groups.has(key)) groups.set(key, { prim, local, normals, entries: [] });
    groups.get(key).entries.push({ node, box });
  }
  const [,level,i,j] = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(tile);
  const origin = [+i*250*2**+level, 0, +j*250*2**+level];
  let extension;
  for (const {prim, local, normals, entries} of groups.values()) {
    if (entries.length < 2) continue;
    extension ??= doc.createExtension(EXTMeshGPUInstancing).setRequired(true);
    const prototype = prim.clone().setAttribute('POSITION', acc('VEC3', local))
      .setAttribute('NORMAL', acc('VEC3', normals));
    const translations=[], rotations=[], scales=[];
    for (const {node,box} of entries) {
      translations.push(...box.center); scales.push(...box.size);
      const angle=Math.atan2(-box.tangent[2],box.tangent[0]);
      rotations.push(0,Math.sin(angle/2),0,Math.cos(angle/2));
      node.setMesh(null);
    }
    const batch=extension.createInstancedMesh()
      .setAttribute('TRANSLATION',acc('VEC3',translations))
      .setAttribute('ROTATION',acc('VEC4',rotations))
      .setAttribute('SCALE',acc('VEC3',scales));
    scene.addChild(doc.createNode('facade-components').setTranslation(origin)
      .setExtras({layer:'buildings',surface:'facade-component',object_id:`${tile}/facade-components/${groups.size}/${scene.listChildren().length}`,
        instance_objects:entries.map(({node}) => node.getExtras())})
      .setMesh(doc.createMesh().addPrimitive(prototype))
      .setExtension('EXT_mesh_gpu_instancing',batch));
  }
}
