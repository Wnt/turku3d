// Read explicit source instance transforms; never infer objects from triangles.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const {MeshoptDecoder}=await import(pathToFileURL(require.resolve('meshoptimizer')).href);
await MeshoptDecoder.ready;
const data=fs.readFileSync(process.argv[2]);
const length=data.readUInt32LE(12), document=JSON.parse(data.subarray(20,20+length));
const binary=data.subarray(28+length), decoded=new Map();
function accessor(index){
 const a=document.accessors[index],view=document.bufferViews[a.bufferView];
 let bytes=decoded.get(a.bufferView);
 if(!bytes){
  const e=view.extensions?.EXT_meshopt_compression;
  if(e){bytes=new Uint8Array(e.count*e.byteStride);MeshoptDecoder.decodeGltfBuffer(bytes,e.count,e.byteStride,binary.subarray(e.byteOffset??0,(e.byteOffset??0)+e.byteLength),e.mode,e.filter);}
  else bytes=binary.subarray(view.byteOffset??0,(view.byteOffset??0)+view.byteLength);
  decoded.set(a.bufferView,bytes);
 }
 if(a.componentType!==5126)throw new Error('Instance transform must be float32');
 const dimensions={VEC3:3,VEC4:4,SCALAR:1}[a.type],stride=view.byteStride??dimensions*4;
 const dv=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),rows=[];
 for(let i=0;i<a.count;i++){const row=[];for(let k=0;k<dimensions;k++)row.push(dv.getFloat32((a.byteOffset??0)+i*stride+k*4,true));rows.push(row);}
 return rows;
}
const result={};
for(let i=0;i<document.nodes.length;i++){
 const node=document.nodes[i],attributes=node.extensions?.EXT_mesh_gpu_instancing?.attributes;
 if(node.extras?.layer==='vegetation'&&attributes){result[i]={};for(const [key,index]of Object.entries(attributes))result[i][key]=accessor(index);}
}
process.stdout.write(JSON.stringify(result));
