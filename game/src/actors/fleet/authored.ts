import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { weldGeometry, weldGeometryAsync } from '../../world/weldGeometry';
import {splitEmbeddedFleetWheels,splitNamedFleetWheels,type AuthoredFleetParts} from './authoredWheelParts';
import {fleetWheelLayout} from './wheelLayouts';
import { AUTHORED_BY_ID } from './registry';

export const AUTHORED_ESTATE = 'fleet-estate-3';
export const ESTATE_WHEELS=Object.entries(fleetWheelLayout(AUTHORED_ESTATE)!.pivots).map(([name,p])=>({name,
  pivot:{x:p[0],y:p[1],z:p[2]},radius:.32,width:.205}));
interface FleetAsset {body:THREE.BufferGeometry;glass:THREE.BufferGeometry|null;parts:AuthoredFleetParts}
const pending = new Map<string, Promise<FleetAsset>>();

type Part = 'all'|'body'|'glass';
/** Bake authored transforms and material roles into shared body or glazing geometry. */
export function flattenFleetAsset(root: THREE.Object3D, part: Part = 'all', exclude?:ReadonlySet<THREE.Object3D>): THREE.BufferGeometry {
  const joined = joinFleetAsset(root, part, exclude);
  return bounded(weldGeometry(joined), joined);
}
/** The same bake, with the vertex weld in a worker (the joined copy's buffers are transferred). */
export async function flattenFleetAssetAsync(root: THREE.Object3D, part: Part = 'all', exclude?:ReadonlySet<THREE.Object3D>) {
  const joined = joinFleetAsset(root, part, exclude);
  return bounded(await weldGeometryAsync(joined), joined);
}
function bounded(geometry: THREE.BufferGeometry, joined: THREE.BufferGeometry) {
  joined.dispose();
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}
function joinFleetAsset(root: THREE.Object3D, part: Part, exclude?:ReadonlySet<THREE.Object3D>): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];
  root.updateMatrixWorld(true);
  root.traverse(object => {
    if (!(object as THREE.Mesh).isMesh) return;
    for(let at:THREE.Object3D|null=object;at;at=at.parent)if(exclude?.has(at))return;
    const mesh = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
    if (Array.isArray(mesh.material)) throw Error('Expected one GLTF material per primitive');
    const material = mesh.material, role = material.name;
    const glass = role === 'fleet_glass' && material.opacity < 1;
    if (part === 'body' && glass || part === 'glass' && !glass) return;
    const names = `${mesh.name} ${role}`.toLowerCase();
    const front = /lamp[-_ ]front|fleet_headlamp/.test(names), tail = /lamp[-_ ]tail|fleet_taillamp/.test(names);
    if (!role.startsWith('fleet_') && !front && !tail) throw Error(`Unexpected fleet material: ${role}`);
    const g = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    g.applyMatrix4(mesh.matrixWorld);
    for (const key of Object.keys(g.attributes)) if (!['position', 'normal'].includes(key)) g.deleteAttribute(key);
    const n = g.getAttribute('position').count, colors = new Float32Array(n * 3), surfaces = new Float32Array(n * 4);
    const paint = role === 'fleet_paint' && !front && !tail;
    const color = front ? new THREE.Color(0xdde6e8) : tail ? new THREE.Color(0xb91108) : paint ? new THREE.Color(1, 1, 1) : material.color;
    const lamp = front ? 1 : tail ? 4 : role === 'fleet_indicator_left' ? 2 : role === 'fleet_indicator_right' ? 3 : 0;
    const positions = g.getAttribute('position'), normals = g.getAttribute('normal');
    for (let i = 0; i < n; i++) {
      // Road splash on the lower panels and a thin top-facing dust layer, baked once into
      // vertices. No extra texture fetches or per-frame noise in the fleet shader.
      const grime = paint ? Math.max(0, Math.min(1, (.65-positions.getY(i))/.45)) : 0;
      const dust = paint ? Math.max(0, normals.getY(i))*.055 : 0;
      const shade = 1-grime*.22-dust*.35;
      colors.set([color.r*shade, color.g*shade*(1-grime*.025), color.b*shade*(1-grime*.06)], i * 3);
      surfaces.set([Number(paint), Math.min(1,material.roughness+grime*.22+dust),
        material.metalness*(1-grime*.55-dust), part === 'glass' ? material.opacity : lamp], i * 4);
    }
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    g.setAttribute('fleetSurface', new THREE.BufferAttribute(surfaces, 4));
    g.clearGroups(); parts.push(g);
  });
  if (!parts.length) throw Error('Empty authored fleet asset');
  const joined = mergeGeometries(parts, false)!;
  for (const part of parts) part.dispose();
  return joined;
}

async function fleetAsset(model:string,lod:number):Promise<FleetAsset|null> {
  const entry = AUTHORED_BY_ID.get(model);
  if (model !== AUTHORED_ESTATE && !entry) return null;
  const key = `${model}/${lod}`;
  let request = pending.get(key);
  if (!request) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const url = entry ? `${entry.assetBase}/lod${lod}.glb` : `/assets/cars/aava-estate/estate_lod${lod}.glb`;
    request = loader.loadAsync(url).then(async gltf => {
      // Named hubs already bake every opaque triangle. Repeating the whole-car bake here
      // expands and welds all of them a second time, even when callers only need split parts.
      gltf.scene.updateMatrixWorld(true);
      const transform = gltf.scene.matrixWorld.clone();
      let body: THREE.BufferGeometry | undefined;
      const parts=await splitNamedFleetWheels(gltf.scene,(root,exclude)=>flattenFleetAssetAsync(root,'body',exclude))
        ??(model===AUTHORED_ESTATE?await splitEmbeddedFleetWheels(body=await flattenFleetAssetAsync(gltf.scene,'body'),ESTATE_WHEELS):null);
      if(!parts)throw Error(`Authored fleet lacks named wheel hubs: ${model}`);
      const layout=fleetWheelLayout(model)!;
      for(const wheel of parts.wheels){wheel.radius=layout.radius;wheel.width=layout.width;}
      parts.wheels.sort((a,b)=>b.pivot.z-a.pivot.z||b.pivot.x-a.pivot.x);
      let hasGlass = false;
      gltf.scene.traverse(object => {
        const m = object as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
        if (m.isMesh && !Array.isArray(m.material) && m.material.name === 'fleet_glass' && m.material.opacity < 1) hasGlass = true;
      });
      const glass = hasGlass ? await flattenFleetAssetAsync(gltf.scene, 'glass') : null;
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
    }).catch(error => { pending.delete(key); throw error; });
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
