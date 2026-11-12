// Retain cached building object ranges without changing any appearance attribute.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);
await MeshoptDecoder.ready;
const input=fs.readFileSync(process.argv[2]), length=input.readUInt32LE(12);
const doc=JSON.parse(input.subarray(20,20+length)), original=input.subarray(28+length);
const bounds=process.argv[5]?JSON.parse(process.argv[5]):null;
const excluded=new Set(JSON.parse(fs.readFileSync(process.argv[4],'utf8')));
const decoded=new Map(), chunks=[original], nodes=[], identities=new Set(), actualBounds={min:[Infinity,Infinity,Infinity],max:[-Infinity,-Infinity,-Infinity]};let size=original.length, triangles=0;
function accessorBytes(index){
 const a=doc.accessors[index],v=doc.bufferViews[a.bufferView];let bytes=decoded.get(a.bufferView);
 if(!bytes){const e=v.extensions?.EXT_meshopt_compression;
  if(e){bytes=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(bytes,e.count,e.byteStride,original.subarray(e.byteOffset??0,(e.byteOffset??0)+e.byteLength),e.mode,e.filter);}
  else {if(v.buffer!==0)throw Error('Resolve external source buffers first');bytes=original.subarray(v.byteOffset??0,(v.byteOffset??0)+v.byteLength);}
  decoded.set(a.bufferView,bytes);
 }
 return [a,v,new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength)];
}
function indices(index){
 const [a,v,view]=accessorBytes(index),width={5123:2,5125:4}[a.componentType];
 if(!width||a.type!=='SCALAR')throw Error('Unsupported source index accessor');
 return Array.from({length:a.count},(_,i)=>width===2?view.getUint16((a.byteOffset??0)+i*width,true):view.getUint32((a.byteOffset??0)+i*width,true));
}
function rangeBounds(primitive,indices,node){
 const [a,v,data]=accessorBytes(primitive.attributes.POSITION);if(a.componentType!==5126||a.type!=='VEC3')throw Error('Unsupported source position accessor');
 const stride=v.byteStride??12,t=node.translation??[0,0,0],low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
 for(const i of indices)for(let k=0;k<3;k++){const value=data.getFloat32((a.byteOffset??0)+i*stride+k*4,true)+t[k];low[k]=Math.min(low[k],value);high[k]=Math.max(high[k],value);}
 return {min:low,max:high};
}
for(const node of doc.nodes){
 const e=node.extras;if(e?.layer!=='buildings')continue;
 if(node.children||node.matrix)throw Error('Building context requires explicit source leaf transforms');
 const mesh=doc.meshes[node.mesh];if(mesh.primitives.length!==1)throw Error('Unresolved multi-primitive object ownership');
 const primitive=mesh.primitives[0], old=indices(primitive.indices);
 const rows=e.objects??[{...e,start:0,count:old.length}];
 let selected=[], retained=[];
 for(const row of rows){
  if(!row.building_id)throw Error('Missing explicit building identity');
  if(excluded.has(row.building_id))continue;
  if(row.start<0||row.count%3||row.start+row.count>old.length)throw Error('Invalid building index range');
  const range=old.slice(row.start,row.start+row.count), box=rangeBounds(primitive,range,node);
  if(bounds&&(box.max[0]<bounds[0]||box.min[0]>bounds[2]||box.max[2]<bounds[1]||box.min[2]>bounds[3]))continue;
  for(let k=0;k<3;k++){actualBounds.min[k]=Math.min(actualBounds.min[k],box.min[k]);actualBounds.max[k]=Math.max(actualBounds.max[k],box.max[k]);}
  retained.push({...row,start:selected.length});for(const index of range)selected.push(index);identities.add(row.building_id);
 }
 if(!selected.length)continue;
 const pad=(-size)&3;if(pad){chunks.push(Buffer.alloc(pad));size+=pad;}
 const bytes=Buffer.alloc(selected.length*4);selected.forEach((v,i)=>bytes.writeUInt32LE(v,i*4));
 const bv=doc.bufferViews.length;doc.bufferViews.push({buffer:0,byteOffset:size,byteLength:bytes.length,target:34963});chunks.push(bytes);size+=bytes.length;
 const ac=doc.accessors.length;doc.accessors.push({bufferView:bv,componentType:5125,count:selected.length,type:'SCALAR'});
 const mi=doc.meshes.length;doc.meshes.push({...mesh,primitives:[{...primitive,indices:ac}]});
 nodes.push({...node,mesh:mi,extras:{...e,...(e.objects?{objects:retained}:{})}});triangles+=selected.length/3;
}
for(const image of doc.images??[])if(image.uri)image.uri=path.basename(image.uri);
const meshes=nodes.map(node=>doc.meshes[node.mesh]);nodes.forEach((node,i)=>node.mesh=i);doc.meshes=meshes;
doc.nodes=nodes;doc.scenes=[{nodes:nodes.map((_,i)=>i)}];doc.scene=0;doc.buffers[0].byteLength=size;
let json=Buffer.from(JSON.stringify(doc));json=Buffer.concat([json,Buffer.alloc((-json.length)&3,32)]);
let binary=Buffer.concat(chunks);binary=Buffer.concat([binary,Buffer.alloc((-binary.length)&3)]);
const head=Buffer.alloc(20);head.writeUInt32LE(0x46546c67);head.writeUInt32LE(2,4);head.writeUInt32LE(28+json.length+binary.length,8);head.writeUInt32LE(json.length,12);head.writeUInt32LE(0x4e4f534a,16);
const binhead=Buffer.alloc(8);binhead.writeUInt32LE(binary.length);binhead.writeUInt32LE(0x004e4942,4);
fs.writeFileSync(process.argv[3],Buffer.concat([head,json,binhead,binary]));
process.stdout.write(JSON.stringify({sourceBytes:original.length,retainedOriginalBinary:true,buildings:[...identities].sort(),nodes:nodes.length,triangles,bounds:actualBounds}));
