import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
export const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(),'turku3d/tools/npm'),'package.json'));
const { NodeIO }=require('@gltf-transform/core');
const { ALL_EXTENSIONS }=require('@gltf-transform/extensions');
const { MeshoptDecoder }=await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
export const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder});
export async function readSource(source, images=true) {
  source=fs.realpathSync(source);
  const raw=fs.readFileSync(source),length=raw.readUInt32LE(12);
  const json=JSON.parse(raw.subarray(20,20+length).toString());
  const resources={'@glb.bin':raw.subarray(28+length)};
  if(json.buffers?.[0]&&!json.buffers[0].uri)json.buffers[0].uri='@glb.bin';
  for(const buffer of json.buffers??[])if(buffer.uri&&buffer.uri!=='@glb.bin')
    resources[buffer.uri]=fs.readFileSync(path.resolve(path.dirname(source),buffer.uri));
  for(const image of json.images??[])if(image.uri)
    resources[image.uri]=images?fs.readFileSync(path.resolve(path.dirname(source),image.uri)):new Uint8Array(4);
  return io.readJSON({json,resources});
}
export const world=(m,p)=>[0,1,2].map(k=>m[k]*p[0]+m[k+4]*p[1]+m[k+8]*p[2]+m[k+12]);

/** The tile visual plus its whole-building HD models translated to their placements (buildingPlacements). */
export async function readComposed(source, placements, images=true) {
  const document=await readSource(source,images);
  if(!placements)return document;
  const { mergeDocuments }=require('@gltf-transform/functions');
  const scene=document.getRoot().getDefaultScene()??document.getRoot().listScenes()[0];
  for(const {path:file,position} of JSON.parse(fs.readFileSync(placements,'utf8'))){
    const model=await readSource(file,images);
    const map=mergeDocuments(document,model);
    for(const added of model.getRoot().listScenes()){
      const copy=map.get(added);
      const holder=document.createNode('building-placement').setTranslation(position);
      for(const child of copy.listChildren()){copy.removeChild(child);holder.addChild(child);}
      scene.addChild(holder);
      copy.dispose();
    }
  }
  return document;
}
