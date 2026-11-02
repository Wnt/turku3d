// Small placement sidecar from the existing source; never read textures or rebuild a tile.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const [source, output] = process.argv.slice(2);
const raw = fs.readFileSync(source), length = raw.readUInt32LE(12);
const json = JSON.parse(raw.subarray(20,20+length).toString());
const resources = { '@glb.bin':raw.subarray(28+length) };
if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri='@glb.bin';
for (const buffer of json.buffers??[]) if (buffer.uri && buffer.uri!=='@glb.bin') resources[buffer.uri]=fs.readFileSync(path.resolve(path.dirname(source),buffer.uri));
for (const image of json.images??[]) if (image.uri) resources[image.uri]=new Uint8Array(4);
const doc = await new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder}).readJSON({json,resources});
const nodes = [], water = new Set();
const world = (m,p) => [0,1,2].map(k=>m[k]*p[0]+m[k+4]*p[1]+m[k+8]*p[2]+m[k+12]);
// Match VegetationLod's 4 m / 14 m water reach so unsurveyed riverside species also match full detail.
for (const node of doc.getRoot().listNodes()) {
  if (!String(node.getExtras().material_key??'').includes('water')) continue;
  for (const primitive of node.getMesh()?.listPrimitives()??[]) {
    const pos=primitive.getAttribute('POSITION'), index=primitive.getIndices(); if (!pos) continue;
    const count=index?.getCount()??pos.getCount(), m=node.getWorldMatrix();
    for (let i=0;i<count;i++) {
      const j=i%3===2?i-2:i+1;
      const a=world(m,pos.getElement(index?.getScalar(i)??i,[])), b=world(m,pos.getElement(index?.getScalar(j)??j,[]));
      const steps=Math.max(1,Math.ceil(Math.hypot(...a.map((v,k)=>v-b[k]))/4));
      for (let s=0;s<=steps;s++) {
        const x=Math.floor((a[0]+(b[0]-a[0])*s/steps)/4),z=Math.floor((a[2]+(b[2]-a[2])*s/steps)/4);
        for(let dz=-4;dz<=4;dz++) for(let dx=-4;dx<=4;dx++) water.add(`${x+dx},${z+dz}`);
      }
    }
  }
}
const represented=new Set();
for (const node of doc.getRoot().listNodes()) {
  const e=node.getExtras(), inst=node.getExtension('EXT_mesh_gpu_instancing');
  if(e.layer!=='vegetation'||!String(e.material_key).includes('foliage')||!inst) continue;
  const variant=String(e.variant??node.getName().replace(/:\d+$/,''));
  const translations=inst.getAttribute('TRANSLATION'); if(!translations) continue;
  const ids=e.instance_object_ids??[], identity=JSON.stringify([variant,ids,node.getWorldMatrix()]);
  if(represented.has(identity)) continue; represented.add(identity);
  const values=(name,size,fallback)=>{
    const attr=inst.getAttribute(name);
    return Array.from({length:translations.getCount()},(_,i)=>attr?attr.getElement(i,[]):Array(size).fill(0).map((_,k)=>fallback[k]));
  };
  const matrix=node.getWorldMatrix(), positions=values('TRANSLATION',3,[0,0,0]);
  nodes.push({variant,matrix,positions,scales:values('SCALE',3,[1,1,1]),rotations:values('ROTATION',4,[0,0,0,1]),ids,
    species:e.instance_species??[],nearWater:positions.map(p=>{const w=world(matrix,p);return water.has(`${Math.floor(w[0]/4)},${Math.floor(w[2]/4)}`);})});
}
fs.writeFileSync(output,JSON.stringify({version:1,nodes}));
