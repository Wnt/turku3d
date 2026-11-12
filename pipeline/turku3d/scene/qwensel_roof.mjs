// Preserve the approved Qwensel roof planes/frontage and append qualified roof detail.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {resolvePreparedAsset} from './asset_store.mjs';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);await MeshoptDecoder.ready;
const dataBytes=fs.readFileSync(new URL('./qwensel_roof.json',import.meta.url));
const data=JSON.parse(dataBytes),sha=b=>crypto.createHash('sha256').update(b).digest('hex');

export function slopeFrame(normal){
 const n=normal.map(x=>x/Math.hypot(...normal));let t=[n[2],0,-n[0]];
 const length=Math.hypot(...t);if(length<1e-5)throw Error('Expected sloping roof plane');t=t.map(x=>x/length);if(t[0]<0)t=t.map(x=>-x);
 const v=[n[1]*t[2]-n[2]*t[1],n[2]*t[0]-n[0]*t[2],n[0]*t[1]-n[1]*t[0]];
 if(v[1]<0)for(let i=0;i<3;i++)v[i]*=-1;
 return {t,v};
}
export function adaptQwenselRoof(bytes,maps){
 if(sha(bytes)!==data.sourceGlbSha256||maps.sourceGlbSha256!==data.sourceGlbSha256)throw Error('Roof source differs from approved Qwensel');
 if(maps.roofDataSha256!==sha(dataBytes))throw Error('Roof maps do not match qualified observations');
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

 const sampler=document.samplers.length;document.samplers.push({magFilter:9729,minFilter:9987,wrapS:10497,wrapT:10497});
 function mappedMaterial(kind){
  const textures={};for(const slot of ['albedo','normal','orm']){
   const m=maps.surfaces[kind][slot];if(!/^[a-f0-9]{64}$/.test(m.sha256)||m.uri!==`../shared/${m.sha256}.ktx2`||m.extent.some(n=>n%4))throw Error('Invalid aligned roof texture');
   const image=document.images.length;document.images.push({mimeType:'image/ktx2',uri:m.uri});textures[slot]=document.textures.length;document.textures.push({sampler,extensions:{KHR_texture_basisu:{source:image}}});
  }
  const index=document.materials.length;document.materials.push({name:`qwensel-roof-${kind}`,extras:{material_key:'landmark_procedural',qualifiedRoofDetail:1},pbrMetallicRoughness:{baseColorTexture:{index:textures.albedo},metallicRoughnessTexture:{index:textures.orm},metallicFactor:0,roughnessFactor:1},normalTexture:{index:textures.normal},occlusionTexture:{index:textures.orm}});return index;
 }
 const clay=mappedMaterial('clay'),brick=mappedMaterial('brick');
 const roofTriangles=[],planes=[];let selectedTriangles=0,roofMesh=null;
 for(const mesh of document.meshes)for(const primitive of mesh.primitives){
  if(document.materials[primitive.material]?.extras?.material_key!==data.roofMaterialKey)continue;
  const p=accessor(primitive.attributes.POSITION),n=accessor(primitive.attributes.NORMAL),idx=accessor(primitive.indices).values,uv=[];
  for(let i=0;i<p.count;i++){
   const normal=n.values.slice(3*i,3*i+3);let plane=planes.find(q=>q.normal.reduce((sum,x,k)=>sum+x*normal[k],0)>.99999);
   if(!plane){plane={normal,...slopeFrame(normal)};planes.push(plane);}
   const world=p.values.slice(3*i,3*i+3).map((x,k)=>x+data.worldPosition[k]);
   uv.push(world.reduce((sum,x,k)=>sum+x*plane.t[k],0)/data.tileRepeatM[0],world.reduce((sum,x,k)=>sum+x*plane.v[k],0)/data.tileRepeatM[1]);
  }
  for(let j=0;j<idx.length;j+=3)roofTriangles.push(idx.slice(j,j+3).map(i=>p.values.slice(i*3,i*3+3)));
  primitive.attributes={...primitive.attributes,TEXCOORD_0:add(uv,'VEC2')};primitive.material=clay;selectedTriangles+=idx.length/3;roofMesh=mesh;
 }
 if(selectedTriangles!==25||!roofMesh)throw Error('Roof primitive selection differs from approved asset');
 function roofHeight(x,z){
  const heights=[];for(const [a,b,c] of roofTriangles){
   const det=(b[2]-c[2])*(a[0]-c[0])+(c[0]-b[0])*(a[2]-c[2]);if(Math.abs(det)<1e-8)continue;
   const u=((b[2]-c[2])*(x-c[0])+(c[0]-b[0])*(z-c[2]))/det,v=((c[2]-a[2])*(x-c[0])+(a[0]-c[0])*(z-c[2]))/det,w=1-u-v;
   if(Math.min(u,v,w)>=-1e-5)heights.push(u*a[1]+v*b[1]+w*c[1]);
  }if(!heights.length)throw Error('Chimney is outside approved roof');return Math.max(...heights);
 }
 const flash=document.materials.length;document.materials.push({name:'qwensel-chimney-flashing',extras:{material_key:'landmark_procedural'},pbrMetallicRoughness:{baseColorFactor:[.38,.42,.43,1],metallicFactor:.75,roughnessFactor:.58}});
 const dark=document.materials.length;document.materials.push({name:'qwensel-chimney-recess',extras:{material_key:'landmark_procedural'},pbrMetallicRoughness:{baseColorFactor:[.018,.014,.012,1],metallicFactor:0,roughnessFactor:.95}});
 const records=[];
 for(const chimney of data.chimneys){
  const groups=new Map(),cx=chimney.centreXZ[0]-data.worldPosition[0],cz=chimney.centreXZ[1]-data.worldPosition[2],yaw=data.chimneyYaw,cos=Math.cos(yaw),sin=Math.sin(yaw);
  const world=(x,y,z)=>[cx+cos*x+sin*z,y,cz-sin*x+cos*z];
  function face(points,material,uvs){
   let g=groups.get(material);if(!g){g={positions:[],normals:[],uv:[],indices:[]};groups.set(material,g);}
   const a=points[0],b=points[1],c=points[2],ab=b.map((x,k)=>x-a[k]),ac=c.map((x,k)=>x-a[k]);let normal=[ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]];const len=Math.hypot(...normal);if(len<1e-9)throw Error('Degenerate chimney face');normal=normal.map(x=>x/len);
   const start=g.positions.length/3;points.forEach((p,i)=>{g.positions.push(...p);g.normals.push(...normal);g.uv.push(...(uvs?.[i]??[p[0]/.96,p[2]/.60]));});for(let i=1;i<points.length-1;i++)g.indices.push(start,start+i,start+i+1);
  }
  function ring(width,depth,y){return [[-width/2,-depth/2],[width/2,-depth/2],[width/2,depth/2],[-width/2,depth/2]].map(([x,z])=>world(x,typeof y==='function'?y(x,z):y,z));}
  function sides(lo,hi,material){for(let i=0;i<4;i++){const j=(i+1)%4,len=Math.hypot(hi[j][0]-hi[i][0],hi[j][2]-hi[i][2]);face([lo[i],hi[i],hi[j],lo[j]],material,[[0,lo[i][1]/.6],[0,hi[i][1]/.6],[len/.96,hi[j][1]/.6],[len/.96,lo[j][1]/.6]]);}}
  const [cw,cd]=chimney.capSizeM,sw=cw-2*chimney.shaftInsetM,sd=cd-2*chimney.shaftInsetM,top=chimney.topM;
  const floor=(x,z)=>{const p=world(x,0,z);return roofHeight(p[0],p[2]);};
  const base=Math.min(...ring(sw,sd,0).map(p=>roofHeight(p[0],p[2])))-.06;
  const bodyLow=ring(sw,sd,base),bodyHigh=ring(sw,sd,top-.16);sides(bodyLow,bodyHigh,brick);
  const capLow=ring(cw,cd,top-.16),capHigh=ring(cw,cd,top);sides(capLow,capHigh,brick);
  const opening=ring(cw-.24,cd-.24,top);for(let i=0;i<4;i++){const j=(i+1)%4;face([capHigh[j],capHigh[i],opening[i],opening[j]],brick);}
  face([...ring(cw-.24,cd-.24,top-.055)].reverse(),dark);
  // Thin, sloped metal apron follows the exact retained roof at every corner.
  const inner=ring(sw+.015,sd+.015,(x,z)=>floor(x,z)+.10),outer=ring(sw+.28,sd+.28,(x,z)=>floor(x,z)+.025);
  for(let i=0;i<4;i++){const j=(i+1)%4;face([outer[j],outer[i],inner[i],inner[j]],flash);}
  for(const [material,g] of groups){
   const attrs={POSITION:add(g.positions,'VEC3'),NORMAL:add(g.normals,'VEC3'),TEXCOORD_0:add(g.uv,'VEC2')};const a=document.accessors[attrs.POSITION];a.min=[0,1,2].map(k=>Math.min(...g.positions.filter((_,i)=>i%3===k)));a.max=[0,1,2].map(k=>Math.max(...g.positions.filter((_,i)=>i%3===k)));
   roofMesh.primitives.push({attributes:attrs,indices:add(g.indices,'SCALAR',5125),material,extras:{qualifiedChimney:chimney.id,collision:'passable'}});
  }
  records.push({id:chimney.id,centreXZ:chimney.centreXZ,topM:top,roofBaseM:base,capSizeM:chimney.capSizeM,triangles:[...groups.values()].reduce((n,g)=>n+g.indices.length/3,0)});
 }
 document.asset.extras={...document.asset.extras,qwenselRoof:{version:1,sourceSha256:data.sourceGlbSha256,roofDataSha256:sha(dataBytes),preserved:'original positions,normals,indices,frontage materials/UVs and roof planes; appended non-colliding chimneys',evidence:data.evidence}};
 document.buffers[0].byteLength=byteLength;
 const json=Buffer.from(JSON.stringify(document)),jp=Buffer.alloc((json.length+3)&~3,32);json.copy(jp);
 const bin=Buffer.concat([binary,...append]),bp=Buffer.alloc((bin.length+3)&~3);bin.copy(bp);
 const out=Buffer.alloc(28+jp.length+bp.length);out.writeUInt32LE(0x46546c67,0);out.writeUInt32LE(2,4);out.writeUInt32LE(out.length,8);out.writeUInt32LE(jp.length,12);out.writeUInt32LE(0x4e4f534a,16);jp.copy(out,20);out.writeUInt32LE(bp.length,20+jp.length);out.writeUInt32LE(0x004e4942,24+jp.length);bp.copy(out,28+jp.length);

 return {bytes:out,report:{sourceSha256:data.sourceGlbSha256,sha256:sha(out),selectedTriangles,roofPlanes:planes.length,chimneys:records,preservedBinaryPrefixBytes:binary.length,externalImageUris:document.images.map(i=>i.uri).filter(Boolean)}};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [input,mapPath,output]=process.argv.slice(2);if(!input||!mapPath||!output)throw Error('Usage: qwensel_roof.mjs INPUT.glb MAPS.json OUTPUT.glb');
 const maps=JSON.parse(fs.readFileSync(mapPath));for(const slots of Object.values(maps.surfaces))for(const m of Object.values(slots))if(sha(fs.readFileSync(m.path))!==m.sha256)throw Error('Map hash differs');
 const r=adaptQwenselRoof(fs.readFileSync(resolvePreparedAsset(input)),maps);fs.writeFileSync(output,r.bytes);console.log(JSON.stringify(r.report));
}
