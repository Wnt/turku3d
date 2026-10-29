// Inspect decoded, shipped triangles across every layer and GPU instance.
import { spawnSync } from 'node:child_process';

export function auditVisual(document) {
  const faces = [], ids = [];
  const layers = {};
  let triangles = 0, instances = 0, programTriangles = 0;
  for (const node of document.getRoot().listNodes()) {
    if (!node.getMesh()) continue;
    const extras = node.getExtras(), matrix = node.getWorldMatrix();
    const batch = node.getExtension('EXT_mesh_gpu_instancing');
    const count = batch ? batch.listAttributes()[0].getCount() : 1;
    instances += batch ? count : 0;
    for (const prim of node.getMesh().listPrimitives()) {
      const position = prim.getAttribute('POSITION');
      const indices = prim.getIndices()?.getArray() ?? Uint32Array.from({length:position.getCount()}, (_, i) => i);
      const fields = prim.listSemantics().filter(key => key.startsWith('_KIT_'));
      for (const key of fields) {
        const attribute = prim.getAttribute(key), values = attribute.getArray(), size = attribute.getElementSize();
        // Compare the exact stored components. Program attributes are floats;
        // no per-corner arrays or normalized copies are needed for equality.
        for (let i = 0; i < indices.length; i += 3) {
          const a = indices[i]*size, b = indices[i+1]*size, c = indices[i+2]*size;
          for (let k = 0; k < size; k++) {
            const value = values[a+k];
            if (!Number.isFinite(value) || values[b+k] !== value || values[c+k] !== value)
              throw new Error(`Mixed shipped facade instruction ${extras.object_id} triangle ${i/3} ${key}`);
          }
        }
      }
      if (fields.length) programTriangles += indices.length / 3;
      for (let instance = 0; instance < count; instance++) {
        const t = batch?.getAttribute('TRANSLATION')?.getElement(instance, []) ?? [0,0,0];
        const r = batch?.getAttribute('ROTATION')?.getElement(instance, []) ?? [0,0,0,1];
        const s = batch?.getAttribute('SCALE')?.getElement(instance, []) ?? [1,1,1];
        const points = Array.from({length:position.getCount()}, (_, i) => {
          const q = position.getElement(i, []).map((x, k) => x*s[k]);
          const cross = [r[1]*q[2]-r[2]*q[1], r[2]*q[0]-r[0]*q[2], r[0]*q[1]-r[1]*q[0]];
          const twice = [r[1]*cross[2]-r[2]*cross[1], r[2]*cross[0]-r[0]*cross[2], r[0]*cross[1]-r[1]*cross[0]];
          const p = q.map((x, k) => x+2*(r[3]*cross[k]+twice[k])+t[k]);
          return [0,1,2].map(k => Math.fround(matrix[k]*p[0]+matrix[4+k]*p[1]+matrix[8+k]*p[2]+matrix[12+k]));
        });
        for (let i = 0; i < indices.length; i += 3) {
          triangles++;
          const a = points[indices[i]], b = points[indices[i+1]], c = points[indices[i+2]];
          const ux=b[0]-a[0], uy=b[1]-a[1], uz=b[2]-a[2];
          const vx=c[0]-a[0], vy=c[1]-a[1], vz=c[2]-a[2];
          const nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx;
          const length = Math.hypot(nx, ny, nz);
          if (length < 1e-12 || Math.abs(ny) >= .02*length) continue;
          const owner = extras.instance_objects?.[instance] ?? extras.objects?.find(o => i >= o.start && i < o.start+o.count) ?? extras;
          const layer = owner.layer ?? extras.layer ?? 'unknown';
          layers[layer] = (layers[layer] ?? 0)+1;
          faces.push([a,b,c]);
          ids.push(`${layer}/${owner.building_id ?? ''}/${owner.object_id ?? node.getName()}/instance:${instance}/face:${i/3}`);
        }
      }
    }
  }
  const result = spawnSync('python', ['-m', 'turku3d.wall_surfaces'], {
    input:JSON.stringify({faces, ids}), encoding:'utf8', maxBuffer:16*1024*1024,
  });
  if (result.status !== 0) throw new Error(`Shipped wall audit failed: ${result.stderr || result.error}`);
  return { ...JSON.parse(result.stdout), triangles, instances, program_triangles:programTriangles, layers };
}
