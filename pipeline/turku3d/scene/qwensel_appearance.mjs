// Replace only the two retained river-wall material regions. All original mesh
// positions, normals, other UVs, roof materials and external image bytes remain.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {resolvePreparedAsset} from './asset_store.mjs';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);await MeshoptDecoder.ready;
const frontageBytes=fs.readFileSync(new URL('./qwensel_frontage.json',import.meta.url));
const frontage=JSON.parse(frontageBytes);
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');

export function adaptQwensel(bytes,maps,expectedSha=frontage.authoredGlbSha256){
 if(sha(bytes)!==expectedSha)throw Error('Qwensel source differs from retained authored asset');
 if(maps.frontageDataSha256!==sha(frontageBytes))throw Error('Frontage map layout differs from reviewed parameters');
 if(maps.sourceGlbSha256!==frontage.authoredGlbSha256)throw Error('Frontage maps belong to another source');
 for(const slot of ['albedo','normal','orm'])if(!/^[a-f0-9]{64}$/.test(maps.maps?.[slot]?.sha256)||maps.maps[slot].uri!==`../shared/${maps.maps[slot].sha256}.ktx2`)throw Error('Invalid immutable frontage map '+slot);
 const length=bytes.readUInt32LE(12),document=JSON.parse(bytes.subarray(20,20+length));
 const binary=bytes.subarray(28+length),decoded=new Map(),append=[];let byteLength=binary.length;
 function accessor(index){
  const a=document.accessors[index],view=document.bufferViews[a.bufferView];let raw=decoded.get(a.bufferView);
  if(!raw){const e=view.extensions?.EXT_meshopt_compression;
   if(e){raw=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(raw,e.count,e.byteStride,binary.subarray(e.byteOffset??0,(e.byteOffset??0)+e.byteLength),e.mode,e.filter);}
   else raw=binary.subarray(view.byteOffset??0,(view.byteOffset??0)+view.byteLength);
   decoded.set(a.bufferView,raw);
  }
  if(a.sparse)throw Error('Sparse source accessor unsupported');
  const dim={SCALAR:1,VEC2:2,VEC3:3,VEC4:4}[a.type],size={5121:1,5123:2,5125:4,5126:4}[a.componentType];
  if(!dim||!size)throw Error('Unsupported source accessor');
  const get={5121:'getUint8',5123:'getUint16',5125:'getUint32',5126:'getFloat32'}[a.componentType];
  const stride=view.byteStride??dim*size,dv=new DataView(raw.buffer,raw.byteOffset,raw.byteLength),values=[];
  for(let i=0;i<a.count;i++)for(let k=0;k<dim;k++)values.push(dv[get]((a.byteOffset??0)+i*stride+k*size,true));
  return {values,dim,count:a.count,type:a.type};
 }
 function add(values,type,componentType=5126){
  const pad=(-byteLength)&3;if(pad){append.push(Buffer.alloc(pad));byteLength+=pad;}
  const raw=Buffer.alloc(values.length*4);values.forEach((v,i)=>componentType===5126?raw.writeFloatLE(v,i*4):raw.writeUInt32LE(v,i*4));
  const view=document.bufferViews.length;document.bufferViews.push({buffer:0,byteOffset:byteLength,byteLength:raw.length});append.push(raw);byteLength+=raw.length;
  const index=document.accessors.length;document.accessors.push({bufferView:view,componentType,count:values.length/{SCALAR:1,VEC2:2,VEC3:3,VEC4:4}[type],type});return index;
 }
 const sampler=document.samplers.length;document.samplers.push({magFilter:9729,minFilter:9987,wrapS:33071,wrapT:33071});
 const textures={};for(const slot of ['albedo','normal','orm']){
  const image=document.images.length;document.images.push({mimeType:'image/ktx2',uri:maps.maps[slot].uri});
  textures[slot]=document.textures.length;document.textures.push({sampler,extensions:{KHR_texture_basisu:{source:image}}});
 }
 const material=document.materials.length;document.materials.push({name:'qwensel-reviewed-river-front',extras:{material_key:'landmark_procedural',reviewedFrontage:1},pbrMetallicRoughness:{baseColorTexture:{index:textures.albedo},metallicRoughnessTexture:{index:textures.orm}},normalTexture:{index:textures.normal},occlusionTexture:{index:textures.orm}});
 const segments=frontage.frontLocalPath.slice(1).map((b,i)=>{const a=frontage.frontLocalPath[i],dx=b[0]-a[0],dz=b[1]-a[1],length=Math.hypot(dx,dz);return {a,b,length,dx:dx/length,dz:dz/length};});
 const total=segments.reduce((n,s)=>n+s.length,0);let selectedTriangles=0,changedPrimitives=0;
 for(const mesh of document.meshes){const additions=[];
  for(const primitive of mesh.primitives){
   const attrs=primitive.attributes;if(attrs.NORMAL===undefined)continue;
   const pos=accessor(attrs.POSITION),normal=accessor(attrs.NORMAL),indices=accessor(primitive.indices).values;
   const selected=[],retained=[];
   const owns=i=>{const x=normal.values[i*3],y=normal.values[i*3+1],z=normal.values[i*3+2];const plane=x*pos.values[i*3]+y*pos.values[i*3+1]+z*pos.values[i*3+2];return x>.48&&x<.50&&Math.abs(y)<.001&&z>.87&&z<.88&&plane>10.45&&plane<10.60;};
   for(let i=0;i<indices.length;i+=3){const tri=indices.slice(i,i+3);(tri.every(owns)?selected:retained).push(...tri);}
   if(!selected.length)continue;if(!retained.length)throw Error('Expected combined front/other-wall source primitive');
   const uv=[];
   for(let i=0;i<pos.count;i++){
    const x=pos.values[i*3],y=pos.values[i*3+1],z=pos.values[i*3+2];let best=Infinity,along=0,offset=0;
    for(const s of segments){const t=Math.max(0,Math.min(s.length,(x-s.a[0])*s.dx+(z-s.a[1])*s.dz));const distance=Math.hypot(x-s.a[0]-s.dx*t,z-s.a[1]-s.dz*t);if(distance<best){best=distance;along=offset+t;}offset+=s.length;}
    uv.push(along/total,1-(y-frontage.baseHeight)/frontage.elevationHeight);
   }
   primitive.indices=add(retained,'SCALAR',5125);
   additions.push({...primitive,attributes:{...attrs,TEXCOORD_0:add(uv,'VEC2')},indices:add(selected,'SCALAR',5125),material});
   selectedTriangles+=selected.length/3;changedPrimitives++;
  }
  mesh.primitives.push(...additions);
 }
 if(changedPrimitives!==1||selectedTriangles!==4)throw Error(`Unexpected retained front selection: ${changedPrimitives} primitives/${selectedTriangles} triangles`);
 document.asset.extras={...document.asset.extras,qwenselFrontage:{version:1,authoredSourceSha256:expectedSha,frontageDataSha256:maps.frontageDataSha256,preserved:'original positions,normals,other-wall UVs,roof and all original image dependencies',confidence:frontage.source.confidence}};
 document.buffers[0].byteLength=byteLength;
 const json=Buffer.from(JSON.stringify(document)),jp=Buffer.alloc((json.length+3)&~3,32);json.copy(jp);
 const bin=Buffer.concat([binary,...append]),bp=Buffer.alloc((bin.length+3)&~3);bin.copy(bp);
 const out=Buffer.alloc(28+jp.length+bp.length);out.writeUInt32LE(0x46546c67,0);out.writeUInt32LE(2,4);out.writeUInt32LE(out.length,8);out.writeUInt32LE(jp.length,12);out.writeUInt32LE(0x4e4f534a,16);jp.copy(out,20);out.writeUInt32LE(bp.length,20+jp.length);out.writeUInt32LE(0x004e4942,24+jp.length);bp.copy(out,28+jp.length);
 return {bytes:out,report:{sourceSha256:expectedSha,sha256:sha(out),changedPrimitives,selectedTriangles,preservedBinaryPrefixBytes:binary.length,externalImageUris:document.images.map(i=>i.uri).filter(Boolean)}};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [input,mapPath,output]=process.argv.slice(2);if(!input||!mapPath||!output)throw Error('Usage: qwensel_appearance.mjs INPUT.glb MAPS.json OUTPUT.glb');
 const maps=JSON.parse(fs.readFileSync(mapPath));for(const m of Object.values(maps.maps))if(sha(fs.readFileSync(m.path))!==m.sha256)throw Error('Map hash differs');
 const result=adaptQwensel(fs.readFileSync(resolvePreparedAsset(input)),maps);fs.writeFileSync(output,result.bytes);console.log(JSON.stringify(result.report));
}
