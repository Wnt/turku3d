#!/usr/bin/env node
// Manual real-source inspection. Screenshots and measurements stay in the sandbox.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const phase = process.argv[2];
if (!['before', 'after'].includes(phase)) throw new Error('usage: bridge-quality.mjs before|after');
const root = path.join(workRoot, 'reports/bridge-quality', phase);
const tiles = fs.readdirSync(root).filter(t => /^t0_/.test(t) && fs.existsSync(path.join(root, t, 'manifest.json')));
const inventory = JSON.parse(fs.readFileSync(path.join(workRoot, 'reports/bridge-quality/after/inventory.json')));
const seen = new Set(), views = [];
for (const [tile, records] of Object.entries(inventory)) {
  if (!Array.isArray(records)) continue;
  for (const r of records) {
    if (seen.has(r.id)) continue;
    seen.add(r.id);
    const [x,z] = r.centre, h = r.height;
    const radius = Math.max(12, r.length * 0.8);
    const label = `${r.id}-${r.name ?? 'landing'}`.replace(/[^a-zA-Z0-9_-]/g, '_');
    const target = [x,h,z];
    views.push({ name: `${label}-top`, tile, target, position: [x,h+radius*1.6,z+0.01] });
    views.push({ name: `${label}-oblique`, tile, target, position: [x-radius*.6,h+radius*.7,z+radius] });
    if (!r.name) continue;
    const a=r.axis?.[0] ?? [r.extent[0],r.extent[1]], b=r.axis?.at(-1) ?? [r.extent[2],r.extent[3]];
    const len=Math.hypot(b[0]-a[0],b[1]-a[1]), dx=(b[0]-a[0])/len, dz=(b[1]-a[1])/len;
    views.push({name:`${label}-street`, tile,target,grounding:'ground',position:[a[0]-dx*7,h+1.7,a[1]-dz*7]});
    views.push({name:`${label}-bank`, tile,target,grounding:'ground',position:[a[0]-dz*20,h+0.5,a[1]+dx*20]});
    views.push({name:`${label}-deck`, tile,target:[b[0],h+1.3,b[1]],grounding:'bridges',position:[x-dx*len*.25-dz*r.width*.25,h+1.7,z-dz*len*.25+dx*r.width*.25]});
  }
}
if (phase === 'before') {
  const fixed = JSON.parse(fs.readFileSync(path.join(workRoot, 'reports/bridge-quality/after/render.json'))).results;
  for (const view of views) {
    const pose = fixed.find(row => row.name === view.name);
    if (!pose) throw new Error(`Render after first: missing fixed camera ${view.name}`);
    view.position = pose.position;
  }
}
const sceneFile='game/__bridge-quality.js', pageFile='game/__bridge-quality.html', link='game/public/__bridge-quality';
const source=`import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { PostProcessing } from './src/render/post';
import { PerformanceMonitor } from './src/render/performance';
import { tintDraft } from './src/render/facadeKit';
import { batchTileShadows } from './src/world/shadowBatch';
import { VegetationLod } from './src/world/vegetationLod';
const renderer=new THREE.WebGPURenderer({antialias:false,trackTimestamp:true});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(55,1280/720,0.1,1500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-10-28T12:00:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium'), models=[];
const surfaces={ground:[],bridges:[]};
for(const tile of ${JSON.stringify(tiles)}){
 const model=(await loader.loadAsync('/__bridge-quality/'+tile+'/vis.glb')).scene;
 model.name=tile;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
 tintDraft(model,renderer);batchTileShadows(model);lod.add(model);scene.add(model);models.push(model);
 model.traverse(o=>{if(!o.isMesh)return;for(let p=o;p;p=p.parent){if(surfaces[p.userData.layer]){surfaces[p.userData.layer].push(o);break;}}});
}
scene.updateMatrixWorld(true);
const perf=new PerformanceMonitor(renderer,'webgpu');
window.probe={async view(v){
 camera.position.set(...v.position);
 if(${phase === 'after'}&&v.grounding){
  const support=(x,z,layer)=>new THREE.Raycaster(new THREE.Vector3(x,1000,z),new THREE.Vector3(0,-1,0)).intersectObjects(surfaces[layer],false)[0];
  let hit=support(camera.position.x,camera.position.z,v.grounding);
  if(!hit&&v.grounding==='ground')hit=support(camera.position.x,camera.position.z,'bridges');
  for(const radius of [3,8,16,32]){
   if(hit)break;
   for(const [dx,dz] of [[radius,0],[-radius,0],[0,radius],[0,-radius]]){
    hit=support(camera.position.x+dx,camera.position.z+dz,v.grounding);
    if(hit){camera.position.x+=dx;camera.position.z+=dz;break;}
   }
  }
  if(!hit)throw new Error('Missing camera support: '+v.name);
  camera.position.y=hit.point.y+1.7;
 }
 camera.lookAt(new THREE.Vector3(...v.target));camera.updateMatrixWorld();
 env.follow(camera.position);env.updateIBL();
 for(const model of models){const [,i,j]=model.name.split('_').map(Number);model.visible=Math.hypot(i*250+125-v.target[0],j*250+125-v.target[2])<450;}
 for(let i=0;i<24;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 perf.reset();for(let i=0;i<30;i++){perf.begin();lod.update(camera);post.render();perf.end();await new Promise(requestAnimationFrame);}
 await perf.completed();await perf.flush();return {...perf.snapshot(),eye:camera.position.toArray()};
}};window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile,source,{flag:'wx'});
  fs.writeFileSync(pageFile,'<link rel="icon" href="data:,"><script type="module" src="/__bridge-quality.js"></script>',{flag:'wx'});
  fs.symlinkSync(root,link);
  browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--disable-background-timer-throttling']});
  const page=await browser.newPage({viewport:{width:1280,height:720}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)errors.push(`${r.status()} ${r.url()}`);});
  await page.goto(`http://127.0.0.1:${port()}/__bridge-quality.html`);
  await page.waitForFunction(()=>window.ready,{}, {timeout:180000});
  const results=[];
  for(const view of views){
    if(!tiles.includes(view.tile))continue;
    const {eye,...stats}=await page.evaluate(v=>window.probe.view(v),view);
    await page.screenshot({path:path.join(root,view.name+'.png')});
    results.push({...view,position:eye,stats});
    fs.writeFileSync(path.join(root,'render.json'),JSON.stringify({phase,tiles,errors,results},null,2));
    console.log(view.name,stats.drawCalls,stats.triangles);
  }
  if(errors.length)throw new Error(JSON.stringify(errors));
}finally{
  await browser?.close();
  for(const file of [sceneFile,pageFile,link])if(fs.existsSync(file))fs.unlinkSync(file);
}
