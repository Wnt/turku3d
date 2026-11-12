// Correct a qualified implausible fallback grid without replacing approved patches.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {resolvePreparedAsset} from './asset_store.mjs';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);
await MeshoptDecoder.ready;
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
export function repairStoreys(bytes,recipe){
 if(sha(bytes)!==recipe.sourceSha256)throw Error('Facade source differs from pinned appearance');
 const {sourceStoreyM,correctedStoreyM,correctedEnvelopeM,storeys}=recipe;
 if(!(sourceStoreyM>0&&sourceStoreyM<2.2&&correctedStoreyM>=2.2&&correctedStoreyM<=5.5&&Number.isInteger(storeys)&&storeys>1&&Math.abs(correctedStoreyM*storeys-correctedEnvelopeM)<1e-7))throw Error('Unsupported registered storey correction');
 const length=bytes.readUInt32LE(12),doc=JSON.parse(bytes.subarray(20,20+length)),binary=bytes.subarray(28+length);
 const decoded=new Map(),chunks=[binary];let size=binary.length,changed=0,preservedPatchVertices=0;
 function read(index){
  const a=doc.accessors[index],v=doc.bufferViews[a.bufferView];let raw=decoded.get(a.bufferView);
  if(!raw){const e=v.extensions?.EXT_meshopt_compression;
   if(e){raw=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(raw,e.count,e.byteStride,binary.subarray(e.byteOffset??0,(e.byteOffset??0)+e.byteLength),e.mode,e.filter);}
   else{if(v.buffer!==0)throw Error('Resolve source buffers before facade adaptation');raw=binary.subarray(v.byteOffset??0,(v.byteOffset??0)+v.byteLength);}
   decoded.set(a.bufferView,raw);
  }
  if(a.componentType!==5126||a.sparse)throw Error('Expected explicit float facade fields');
  const dim={VEC3:3,VEC4:4}[a.type],stride=v.byteStride??dim*4,data=new DataView(raw.buffer,raw.byteOffset,raw.byteLength),values=[];
  if(!dim)throw Error('Unsupported facade attribute shape');
  for(let i=0;i<a.count;i++)for(let k=0;k<dim;k++)values.push(data.getFloat32((a.byteOffset??0)+i*stride+k*4,true));
  return {values,dim,count:a.count,type:a.type};
 }
 function append(a){
  const pad=(-size)&3;if(pad){chunks.push(Buffer.alloc(pad));size+=pad;}
  const raw=Buffer.alloc(a.values.length*4);a.values.forEach((v,i)=>raw.writeFloatLE(v,i*4));
  const view=doc.bufferViews.length;doc.bufferViews.push({buffer:0,byteOffset:size,byteLength:raw.length});chunks.push(raw);size+=raw.length;
  const index=doc.accessors.length;doc.accessors.push({bufferView:view,componentType:5126,count:a.count,type:a.type});return index;
 }
 for(const mesh of doc.meshes)for(const primitive of mesh.primitives){
  const attrs=primitive.attributes;if(attrs._KIT_LAYOUT===undefined)continue;
  const layout=read(attrs._KIT_LAYOUT),floors=read(attrs._KIT_FLOORS),detail=read(attrs._KIT_DETAIL);let edits=0;
  if(layout.count!==floors.count||layout.count!==detail.count)throw Error('Mismatched facade attribute ranges');
  for(let i=0;i<layout.count;i++){
   if(detail.values[i*detail.dim+3]>0){preservedPatchVertices++;continue;}
   if(Math.abs(layout.values[i*layout.dim+2]-sourceStoreyM)>1e-5)continue;
   const ground=floors.values[i*floors.dim],upper=floors.values[i*floors.dim+1];
   if(!(upper>0&&upper<2.2))throw Error('Pinned invalid fallback pitch has a different upper-floor policy');
   const nextGround=Math.abs(ground-sourceStoreyM)<1e-5?correctedStoreyM:ground;
   const nextUpper=(correctedEnvelopeM-nextGround)/(storeys-1);
   if(nextUpper<2.2||nextUpper>5.5)throw Error('Reviewed ground floor leaves an implausible upper pitch');
   layout.values[i*layout.dim+2]=correctedStoreyM;floors.values[i*floors.dim]=nextGround;floors.values[i*floors.dim+1]=nextUpper;edits++;
  }
  if(edits){attrs._KIT_LAYOUT=append(layout);attrs._KIT_FLOORS=append(floors);changed+=edits;}
 }
 if(!changed)throw Error('Pinned invalid fallback grid absent');
 doc.asset.extras={...doc.asset.extras,storeyRepair:{...recipe,preserved:'original binary, geometry, UVs, palette, images and explicit observed patches'}};doc.buffers[0].byteLength=size;
 let json=Buffer.from(JSON.stringify(doc));json=Buffer.concat([json,Buffer.alloc((-json.length)&3,32)]);
 let bin=Buffer.concat(chunks);bin=Buffer.concat([bin,Buffer.alloc((-bin.length)&3)]);
 const header=Buffer.alloc(20);header.writeUInt32LE(0x46546c67);header.writeUInt32LE(2,4);header.writeUInt32LE(28+json.length+bin.length,8);header.writeUInt32LE(json.length,12);header.writeUInt32LE(0x4e4f534a,16);
 const binhead=Buffer.alloc(8);binhead.writeUInt32LE(bin.length);binhead.writeUInt32LE(0x004e4942,4);
 const result=Buffer.concat([header,json,binhead,bin]);
 return {bytes:result,report:{sourceSha256:recipe.sourceSha256,sha256:sha(result),changedVertices:changed,preservedPatchVertices,preservedBinaryPrefixBytes:binary.length,externalImageUris:(doc.images??[]).map(i=>i.uri).filter(Boolean)}};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [input,recipe,output]=process.argv.slice(2);if(!input||!recipe||!output)throw Error('Usage: facade_storeys.mjs INPUT.glb RECIPE.json OUTPUT.glb');
 const result=repairStoreys(fs.readFileSync(resolvePreparedAsset(input)),JSON.parse(fs.readFileSync(recipe,'utf8')));fs.writeFileSync(output,result.bytes);console.log(JSON.stringify(result.report));
}
