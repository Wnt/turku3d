/** Offline direct street lighting on final prepared receivers. Run with --experimental-wasm-modules.
 * Rapier uses explicit solid meshes or qualified optical proxies; no inferred blockers or height masks.
 * RGB is outgoing Lambert radiance per unit diffuse albedo, including 1/pi, at full night.
 */
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

// Rapier 0.21 publishes extensionless ESM imports. Scope Node's resolver adjustment to that package.
const hooks = registerHooks({ resolve(specifier, context, next) {
  try { return next(specifier, context); } catch (error) {
    if (!context.parentURL?.includes('/@dimforge/rapier3d/') || !specifier.startsWith('.')) throw error;
    if (error.code === 'ERR_UNSUPPORTED_DIR_IMPORT') return next(specifier + '/index.js', context);
    if (error.code === 'ERR_MODULE_NOT_FOUND' && !/\.[a-z]+$/.test(specifier)) return next(specifier + '.js', context);
    throw error;
  }
} });
const R = await import('../../../game/node_modules/@dimforge/rapier3d/rapier.js');
hooks.deregister();

const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
export function apertureSamples(emitter, resolution = 4) {
  const a = emitter.aperture;
  if (!a) return [{ position: emitter.position, weight: 1, normal: null }];
  if (!Number.isInteger(resolution) || resolution < 2 || resolution > 16) throw Error('Aperture resolution must be 2..16');
  const axisV = cross(a.normal, a.axisU), samples = [];
  for (let u = 0; u < resolution; u++) for (let v = 0; v < resolution; v++) {
    const x = ((u+.5)/resolution-.5)*a.size[0], y = ((v+.5)/resolution-.5)*a.size[1];
    if (x*x+y*y < a.cutoutRadius*a.cutoutRadius) continue;
    samples.push({ position: a.position.map((p, i) => p+x*a.axisU[i]+y*axisV[i]), normal: a.normal });
  }
  if (!samples.length) throw Error('Aperture cutout leaves no samples');
  // Intensity is the total on-axis prior: quadrature divides it, never duplicates lamp output.
  return samples.map(sample => ({ ...sample, weight: 1/samples.length }));
}

export function bake(scene, { resolution = 4 } = {}) {
  const start = performance.now(), world = new R.World({ x: 0, y: 0, z: 0 });
  let triangles = 0, rays = 0, blocked = 0, vertices = 0, litVertices = 0;
  try {
    for (const mesh of scene.meshes) if ((mesh.lightOcclusion ?? (mesh.collision === 'solid' ? 'solid' : 'none')) === 'solid') {
      // Preserve sub-centimetre edges before Float32 conversion, as gameplay's cell-local collision does.
      const origin = mesh.positions.slice(0,3);
      const positions = new Float32Array(mesh.positions.map((p,i) => p-origin[i%3]));
      world.createCollider(R.ColliderDesc.trimesh(positions, new Uint32Array(mesh.indices)).setTranslation(...origin));
      triangles += mesh.indices.length/3;
    }
    world.step();
    const buildMs = performance.now()-start;
    const lights = scene.emitters.map(e => ({ ...e, samples: apertureSamples(e, resolution) }));
    const materials = new Map(scene.materials.map(m => [m.id, m])), values = {};
    for (const mesh of scene.meshes) {
      if (mesh.render === false || materials.get(mesh.materialId)?.water || mesh.materialId === 'glass') continue;
      if (!mesh.normals || mesh.normals.length !== mesh.positions.length) throw Error(`Receiver ${mesh.id} needs prepared normals`);
      const out = new Array(mesh.positions.length).fill(0);
      for (let i = 0; i < mesh.positions.length; i += 3) {
        const p = mesh.positions.slice(i, i+3), n = mesh.normals.slice(i, i+3);
        // Move the receiver outside its own face; the producer already offsets the optical aperture once.
        const origin = { x: p[0]+n[0]*.01, y: p[1]+n[1]*.01, z: p[2]+n[2]*.01 };
        for (const e of lights) for (const s of e.samples) {
          const dx=s.position[0]-p[0], dy=s.position[1]-p[1], dz=s.position[2]-p[2];
          const d2=dx*dx+dy*dy+dz*dz, d=Math.sqrt(d2);
          if (d >= e.range || d < .05 || e.intensity === 0) continue;
          const receiving=(n[0]*dx+n[1]*dy+n[2]*dz)/d;
          const emitting=s.normal ? -(s.normal[0]*dx+s.normal[1]*dy+s.normal[2]*dz)/d : 1;
          if (receiving <= 0 || emitting <= 0) continue;
          const vector={ x:s.position[0]-origin.x, y:s.position[1]-origin.y, z:s.position[2]-origin.z };
          const length=Math.hypot(vector.x,vector.y,vector.z);
          vector.x/=length; vector.y/=length; vector.z/=length;
          rays++;
          if (world.castRay(new R.Ray(origin,vector),Math.max(0,length-.001),true)) { blocked++; continue; }
          const window=Math.max(0,1-(d2/(e.range*e.range))**2)**2;
          const energy=e.intensity*s.weight*receiving*emitting*window/(Math.max(d2,.01)*Math.PI);
          for (let c=0;c<3;c++) out[i+c]+=e.color[c]*energy;
        }
        vertices++; if (out[i]+out[i+1]+out[i+2]>1e-8) litVertices++;
      }
      values[mesh.id]=out;
    }
    return { values, audit: { triangles, vertices, litVertices, rays, blocked, emitters: lights.length,
      apertureResolution: resolution, buildMs, bakeMs: performance.now()-start-buildMs } };
  } finally { world.free(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const scene = JSON.parse(readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify(bake(scene)));
}
