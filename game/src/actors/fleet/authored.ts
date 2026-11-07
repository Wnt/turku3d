import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { weldVertexSteps } from '../../render/weldVertices';
import { drain } from '../../world/shadowChunks';
import { streamingBudget } from '../../render/preparationBudget';
import {splitNamedFleetWheels,type AuthoredFleetParts} from './authoredWheelParts';
import {fleetWheelLayout} from './wheelLayouts';
import { AUTHORED_BY_ID } from './registry';

interface FleetAsset {body:THREE.BufferGeometry;glass:THREE.BufferGeometry|null;parts:AuthoredFleetParts}
const pending = new Map<string, Promise<FleetAsset|null>>();
/** Models whose asset failed after the fetch retries (core/retryFetch.ts): drawn as a fallback model (fleet/visuals.ts). */
const unavailable = new Set<string>();

/** Bake authored transforms and material roles into shared body or glazing geometry. */
export function flattenFleetAsset(root: THREE.Object3D, part: 'all'|'body'|'glass' = 'all', exclude?:ReadonlySet<THREE.Object3D>): THREE.BufferGeometry {
  return drain(flattenFleetSteps(root, part, exclude));
}
/** flattenFleetAsset a mesh, the merge and a weld slice at a time: a big LOD0 (an ambulance, 59k triangles) took
 *  ~90 ms in one piece on the M1 Max even after the welder, inside the meshopt decoder's message task (verify-all r4:
 *  210-342 ms Worker.onmessage frames on Windows right after spawn, while the parked fleet loads). */
export function* flattenFleetSteps(root: THREE.Object3D, part: 'all'|'body'|'glass' = 'all', exclude?:ReadonlySet<THREE.Object3D>): Generator<void, THREE.BufferGeometry> {
  const parts: THREE.BufferGeometry[] = [];
  root.updateMatrixWorld(true);
  const meshes: THREE.Object3D[] = [];
  root.traverse(object => { if ((object as THREE.Mesh).isMesh) meshes.push(object); });
  for (const object of meshes) {
    yield;
    if (exclude && excluded(object, exclude)) continue;
    const mesh = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
    if (Array.isArray(mesh.material)) throw Error('Expected one GLTF material per primitive');
    const material = mesh.material, role = material.name;
    const glass = role === 'fleet_glass' && material.opacity < 1;
    if (part === 'body' && glass || part === 'glass' && !glass) continue;
    const names = `${mesh.name} ${role}`.toLowerCase();
    const front = /lamp[-_ ]front|fleet_headlamp/.test(names), tail = /lamp[-_ ]tail|fleet_taillamp/.test(names);
    if (!role.startsWith('fleet_') && !front && !tail) throw Error(`Unexpected fleet material: ${role}`);
    const g = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    // A big body mesh alone was the longest step (~25 ms for a 59k-triangle LOD0 on the M1 Max): expand, transform and
    // bake it as separate steps.
    yield;
    g.applyMatrix4(mesh.matrixWorld);
    yield;
    // Converted source cars carry their sampled base-colour textures as COLOR_0; authored ones have none.
    const baked = g.getAttribute('color');
    for (const key of Object.keys(g.attributes)) if (!['position', 'normal'].includes(key)) g.deleteAttribute(key);
    const n = g.getAttribute('position').count, colors = new Float32Array(n * 3), surfaces = new Float32Array(n * 4);
    const paint = role === 'fleet_paint' && !front && !tail;
    const color = front ? new THREE.Color(0xdde6e8) : tail ? new THREE.Color(0xb91108) : paint ? new THREE.Color(1, 1, 1) : material.color;
    const lamp = front ? 1 : tail ? 4 : role === 'fleet_indicator_left' ? 2 : role === 'fleet_indicator_right' ? 3 : 0;
    const positions = g.getAttribute('position'), normals = g.getAttribute('normal');
    const tinted = !!baked && !paint && !front && !tail, kind = Number(paint), last = part === 'glass' ? material.opacity : lamp;
    // Written in place: two array literals per vertex were most of a big car's bake (frame-mp).
    for (let i = 0; i < n; i++) {
      if (i % 16384 === 16383) yield;
      // Road splash on the lower panels and a thin top-facing dust layer, baked once into
      // vertices. No extra texture fetches or per-frame noise in the fleet shader.
      const grime = paint ? Math.max(0, Math.min(1, (.65-positions.getY(i))/.45)) : 0;
      const dust = paint ? Math.max(0, normals.getY(i))*.055 : 0;
      const shade = 1-grime*.22-dust*.35;
      const r = tinted ? baked.getX(i) : 1, gr = tinted ? baked.getY(i) : 1, bl = tinted ? baked.getZ(i) : 1;
      const c = i * 3, f = i * 4;
      colors[c] = color.r*r*shade; colors[c + 1] = color.g*gr*shade*(1-grime*.025); colors[c + 2] = color.b*bl*shade*(1-grime*.06);
      surfaces[f] = kind; surfaces[f + 1] = Math.min(1,material.roughness+grime*.22+dust);
      surfaces[f + 2] = material.metalness*(1-grime*.55-dust); surfaces[f + 3] = last;
    }
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    g.setAttribute('fleetSurface', new THREE.BufferAttribute(surfaces, 4));
    g.clearGroups(); parts.push(g);
  }
  if (!parts.length) throw Error('Empty authored fleet asset');
  yield;
  const joined = mergeGeometries(parts, false)!;
  for (const part of parts) part.dispose();
  yield;
  const geometry = yield* weldVertexSteps(joined);
  joined.dispose();
  yield;
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}
function excluded(object: THREE.Object3D, exclude: ReadonlySet<THREE.Object3D>) {
  for (let at: THREE.Object3D | null = object; at; at = at.parent) if (exclude.has(at)) return true;
  return false;
}

const budget = streamingBudget();
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));
/** Runs `steps` to completion, each step within the frame's streaming window. */
async function paced<T>(steps: Generator<void, T>): Promise<T> {
  for (let next = steps.next(); ; next = steps.next()) {
    if (next.done) return next.value;
    let pause = budget.checkpoint();
    while (pause) { await pause; pause = budget.checkpoint(); }
  }
}

async function fleetAsset(model:string,lod:number):Promise<FleetAsset|null> {
  const entry = AUTHORED_BY_ID.get(model);
  if (!entry || unavailable.has(model)) return null;
  const key = `${model}/${lod}`;
  let request = pending.get(key);
  if (!request) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const url = `${entry.assetBase}/lod${lod}.glb`;
    request = loader.loadAsync(url).then(async gltf => {
      // The bake runs in slices inside the frame's streaming window, from a task of its own (not the meshopt
      // decoder's message that resolved the load).
      await nextTask();
      const parts = await paced(splitNamedFleetWheels(gltf.scene, (root, exclude) => flattenFleetSteps(root, 'body', exclude)));
      // Named hubs already bake every opaque triangle. Repeating the whole-car bake here
      // expands and welds all of them a second time, even when callers only need split parts.
      gltf.scene.updateMatrixWorld(true);
      const transform = gltf.scene.matrixWorld.clone();
      let body: THREE.BufferGeometry | undefined;
      if(!parts)throw Error(`Authored fleet lacks named wheel hubs: ${model}`);
      const layout=fleetWheelLayout(model)!;
      for(const wheel of parts.wheels){wheel.radius=layout.radius;wheel.width=layout.width;}
      parts.wheels.sort((a,b)=>b.pivot.z-a.pivot.z||b.pivot.x-a.pivot.x);
      let hasGlass = false;
      gltf.scene.traverse(object => {
        const m = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
        if (m.isMesh && !Array.isArray(m.material) && m.material.name === 'fleet_glass' && m.material.opacity < 1) hasGlass = true;
      });
      const glass = hasGlass ? await paced(flattenFleetSteps(gltf.scene, 'glass')) : null;
      gltf.scene.traverse(object => {
        const mesh = object as THREE.Mesh;
        if (mesh.isMesh) { mesh.geometry.dispose(); for (const material of [mesh.material].flat()) material.dispose(); }
      });
      return { get body() {
        if (!body) {
          const wheels = parts.wheels.map(wheel => wheel.geometry.clone().translate(wheel.pivot.x,wheel.pivot.y,wheel.pivot.z));
          body = mergeGeometries([parts.body,...wheels],false)!;
          for (const wheel of wheels) wheel.dispose();
          body.applyMatrix4(transform);
          body.computeBoundingBox(); body.computeBoundingSphere();
        }
        return body;
      }, glass, parts };
    }).catch(error => {
      // An optional car model never aborts the load: it is drawn as a fallback model, with one warning.
      if (!unavailable.has(model)) console.warn(`[fleet] ${url} unavailable, drawn as a fallback model: ${String(error?.message ?? error)}`);
      unavailable.add(model);
      return null;
    });
    pending.set(key, request);
  }
  return request;
}

export async function loadAuthoredFleet(model:string,lod:number,part:'body'|'glass'='body') {
  return (await fleetAsset(model,lod))?.[part]?.clone()??null;
}
export async function loadAuthoredFleetParts(model:string,lod:number):Promise<AuthoredFleetParts|null> {
  const asset=await fleetAsset(model,lod);if(!asset)return null;
  return {body:asset.parts.body.clone(),wheels:asset.parts.wheels.map(w=>({...w,geometry:w.geometry.clone()}))};
}
