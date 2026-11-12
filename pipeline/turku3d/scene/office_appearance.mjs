// Qualified Petrelius paint/metal appearance correction. Keep every original
// geometry/UV/program-layout byte and external image dependency unchanged.
import fs from 'node:fs';
import {resolvePreparedAsset} from './asset_store.mjs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);
await MeshoptDecoder.ready;
const anchors=JSON.parse(fs.readFileSync(new URL('./office_detail_anchors.json',import.meta.url)));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
export const OFFICE_PALETTE={wall:[.62,.515,.365],trim:[.38,.30,.20],frame:[.19,.145,.09]};

export function adaptOffice(bytes,expectedSha=anchors.authoredGlbSha256){
 if(sha(bytes)!==expectedSha)throw Error('Office source differs from approved authored grid');
 const length=bytes.readUInt32LE(12),document=JSON.parse(bytes.subarray(20,20+length));
 const binary=bytes.subarray(28+length),decoded=new Map(),append=[];
 let byteLength=binary.length,changedVertices=0;
 function accessor(index){
  const a=document.accessors[index],view=document.bufferViews[a.bufferView];
  let raw=decoded.get(a.bufferView);
  if(!raw){
   const e=view.extensions?.EXT_meshopt_compression;
   if(e){raw=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(raw,e.count,e.byteStride,binary.subarray(e.byteOffset??0,(e.byteOffset??0)+e.byteLength),e.mode,e.filter);}
   else raw=binary.subarray(view.byteOffset??0,(view.byteOffset??0)+view.byteLength);
   decoded.set(a.bufferView,raw);
  }
  if(a.componentType!==5126||a.sparse)throw Error('Office appearance requires explicit float attributes');
  const dim={VEC2:2,VEC3:3,VEC4:4,SCALAR:1}[a.type],stride=view.byteStride??dim*4;
  const dv=new DataView(raw.buffer,raw.byteOffset,raw.byteLength),values=[];
  for(let i=0;i<a.count;i++)for(let k=0;k<dim;k++)values.push(dv.getFloat32((a.byteOffset??0)+i*stride+k*4,true));
  return {values,dim,count:a.count,type:a.type};
 }
 function replacement(a){
  const pad=(-byteLength)&3;if(pad){append.push(Buffer.alloc(pad));byteLength+=pad;}
  const raw=Buffer.alloc(a.values.length*4);a.values.forEach((v,i)=>raw.writeFloatLE(v,i*4));
  const view=document.bufferViews.length;document.bufferViews.push({buffer:0,byteOffset:byteLength,byteLength:raw.length});append.push(raw);byteLength+=raw.length;
  const index=document.accessors.length;document.accessors.push({bufferView:view,componentType:5126,count:a.count,type:a.type});return index;
 }
 let patchedRoof=false;
 for(const mesh of document.meshes)for(const primitive of mesh.primitives){
  const attrs=primitive.attributes;
  if(attrs._KIT_LAYOUT!==undefined){
   const positions=accessor(attrs.POSITION);const selection=[];
   // Authored southwest grey wing ends at this retained facade corner. Keep
   // its palette unchanged; the cream body wraps into the photographed court.
   for(let i=0;i<positions.count;i++)selection.push(positions.values[i*3]*.87356-positions.values[i*3+2]*.48672> -9.77);
   changedVertices+=selection.filter(Boolean).length;
   for(const [name,color] of [['_KIT_WALL',OFFICE_PALETTE.wall],['_KIT_BASE',OFFICE_PALETTE.wall],['_KIT_TRIM',OFFICE_PALETTE.trim],['_KIT_FRAME',OFFICE_PALETTE.frame]]){
    if(attrs[name]===undefined)throw Error('Office paint attribute missing '+name);
    const a=accessor(attrs[name]);for(let i=0;i<a.count;i++)if(selection[i])for(let k=0;k<3;k++)a.values[i*a.dim+k]=color[k];
    attrs[name]=replacement(a);
   }
  }else{
   const material=document.materials[primitive.material];
   if(material.extras?.material_key==='roof_ortho'){
    material.pbrMetallicRoughness={...material.pbrMetallicRoughness,baseColorFactor:[.12,.135,.15,1],roughnessFactor:.52,metallicFactor:0};patchedRoof=true;
   }
  }
 }
 if(!changedVertices||!patchedRoof)throw Error('Expected authored office facade and roof not found');
 document.asset.extras={...document.asset.extras,officeAppearance:{version:1,authoredSourceSha256:expectedSha,confidence:'restored multi-view visual palette prior; photographs not photometrically calibrated',preserved:'original geometry,UVs,program layout,external images; southwest grey wing paint unchanged'}};
 document.buffers[0].byteLength=byteLength;
 const json=Buffer.from(JSON.stringify(document)),jp=Buffer.alloc((json.length+3)&~3,32);json.copy(jp);
 const bin=Buffer.concat([binary,...append]),bp=Buffer.alloc((bin.length+3)&~3);bin.copy(bp);
 const out=Buffer.alloc(28+jp.length+bp.length);out.writeUInt32LE(0x46546c67,0);out.writeUInt32LE(2,4);out.writeUInt32LE(out.length,8);out.writeUInt32LE(jp.length,12);out.writeUInt32LE(0x4e4f534a,16);jp.copy(out,20);out.writeUInt32LE(bp.length,20+jp.length);out.writeUInt32LE(0x004e4942,24+jp.length);bp.copy(out,28+jp.length);
 return {bytes:out,report:{sourceSha256:expectedSha,sha256:sha(out),changedVertices,externalImageUris:document.images.map(i=>i.uri).filter(Boolean),preservedBinaryPrefixBytes:binary.length}};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const args=process.argv.slice(2),index=args.indexOf('--asset-root');let assetRoot;
 if(index>=0){assetRoot=args[index+1];args.splice(index,2);}
 const [input,output]=args;if(!input||!output)throw Error('Usage: office_appearance.mjs INPUT.glb OUTPUT.glb [--asset-root ROOT]');
 const result=adaptOffice(fs.readFileSync(resolvePreparedAsset(input,{assetRoot})));fs.writeFileSync(output,result.bytes);console.log(JSON.stringify(result.report));
}
