import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import{createRequire}from'node:module';import{pathToFileURL}from'node:url';
const require=createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS??path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const dep=async name=>import(pathToFileURL(require.resolve(name)).href);
const {NodeIO}=await dep('@gltf-transform/core'),{ALL_EXTENSIONS}=await dep('@gltf-transform/extensions'),{MeshoptDecoder}=await dep('meshoptimizer');await MeshoptDecoder.ready;
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder});const doc=await io.read(process.argv[2]);const rows=[];
for(const node of doc.getRoot().listNodes())for(const prim of node.getMesh()?.listPrimitives()??[]){
 const pa=prim.getAttribute('POSITION'),ca=prim.getAttribute('COLOR_0'),matrix=node.getWorldMatrix(),positions=[],colors=[];
 for(let i=0;i<pa.getCount();i++){const [x,y,z]=pa.getElement(i,[]);positions.push([matrix[0]*x+matrix[4]*y+matrix[8]*z+matrix[12],matrix[1]*x+matrix[5]*y+matrix[9]*z+matrix[13],matrix[2]*x+matrix[6]*y+matrix[10]*z+matrix[14]]);colors.push(ca?ca.getElement(i,[]).slice(0,3):[.5,.5,.5]);}
 rows.push({name:node.getName(),layer:node.getExtras().layer,positions,colors,indices:Array.from(prim.getIndices().getArray())});
}
fs.writeFileSync(process.argv[3],JSON.stringify(rows));
