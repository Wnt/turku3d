// Snapshot world positions and triangle topology before codecs; validates each actual packaged tile.
export function snapshot(d) {
  const result = [];
  for (const n of d.getRoot().listNodes()) {
    if (!n.getMesh()) continue;
    const mat = n.getWorldMatrix();
    const batch = n.getExtension('EXT_mesh_gpu_instancing');
    const count = batch ? batch.listAttributes()[0].getCount() : 1;
    const transforms = Array.from({ length: count }, (_, i) => ({
      t: batch?.getAttribute('TRANSLATION')?.getElement(i, []) ?? [0,0,0],
      r: batch?.getAttribute('ROTATION')?.getElement(i, []) ?? [0,0,0,1],
      s: batch?.getAttribute('SCALE')?.getElement(i, []) ?? [1,1,1],
    }));
    for (const p of n.getMesh().listPrimitives()) {
      const a = p.getAttribute('POSITION');
      const v = [];
      for (const { t, r, s } of transforms) for (let i=0; i<a.getCount(); i++) {
        const q = a.getElement(i, []).map((x,k) => x*s[k]);
        // Quaternion rotation, then translation, then the complete node world matrix.
        const uv = [r[1]*q[2]-r[2]*q[1], r[2]*q[0]-r[0]*q[2], r[0]*q[1]-r[1]*q[0]];
        const uuv = [r[1]*uv[2]-r[2]*uv[1], r[2]*uv[0]-r[0]*uv[2], r[0]*uv[1]-r[1]*uv[0]];
        const local = q.map((x,k) => x+2*(r[3]*uv[k]+uuv[k])+t[k]);
        v.push(...[0,1,2].map(k => mat[k]*local[0]+mat[4+k]*local[1]+mat[8+k]*local[2]+mat[12+k]));
      }
      const ix = Array.from(p.getIndices()?.getArray() ?? Uint32Array.from({length:a.getCount()}, (_,i)=>i));
      // Index codec may rotate a triangle cyclically without changing winding.
      for (let i=0;i<ix.length;i+=3) {
        const t=ix.slice(i,i+3), j=t.indexOf(Math.min(...t)); ix.splice(i,3,...t.slice(j),...t.slice(0,j));
      }
      result.push({ id:n.getExtras().object_id, v, ix, instances:count });
    }
  }
  return result;
}

export function compareSnapshots(before, after) {
  let error = 0;
  if (before.length !== after.length) throw new Error('Mesh count changed during compression');
  for (let i=0;i<before.length;i++) {
    if (before[i].id !== after[i].id || before[i].v.length !== after[i].v.length || JSON.stringify(before[i].ix)!==JSON.stringify(after[i].ix)) throw new Error('Geometry topology changed during compression');
    for (let j=0;j<before[i].v.length;j++) error=Math.max(error, Math.abs(before[i].v[j]-after[i].v[j]));
  }
  if (error > 0.002) throw new Error(`Visual precision exceeds 2 mm: ${error}`);
  return error;
}
