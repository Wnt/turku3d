#!/usr/bin/env node
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
// Manual real-data inspection of parked cars, deliberately outside the test gate.
// Usage: node qa/render/parked-sanity.mjs PHASE [NIGHT] [legacy]  with built tiles (manifest.json,
// vis.glb and ../shared) under $TURKU3D_WORK/reports/parked-sanity/PHASE and the game's Vite dev
// server on this sandbox's port. Renders a top view of every tile and the street views below with the
// client's own parked-car batches at their draft slots, as shown at night level NIGHT (0 day, 1
// night). `legacy` draws the two newer bodies as their older siblings, for the draw-count delta.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const phase = process.argv[2];
const night = Number(process.argv[3] ?? 0);
const legacy = process.argv[4] === 'legacy';
if (!phase || !Number.isFinite(night)) throw new Error('usage: node qa/render/parked-sanity.mjs PHASE [NIGHT]');
const root = path.join(workRoot, 'reports/parked-sanity', phase);
const tiles = fs.readdirSync(root).filter(t => /^t0_/.test(t) && fs.existsSync(path.join(root, t, 'manifest.json')));
const sceneFile = 'game/__parked-sanity.js', pageFile = 'game/__parked-sanity.html';
const link = 'game/public/__parked-sanity';
// Street views: eye 1.7 m above the lowest surface, grid bearing (0 = north) and pitch in degrees.
// Detail views look straight down from `height` metres.
const spots = [
  { name: 'heikkilankatu', tile: 't0_-5_1', at: [-1064.91, 317.01], yaw: 146.7, pitch: -4 },
  { name: 'heikkilankatu-back', tile: 't0_-5_1', at: [-1064.91, 317.01], yaw: 326.7, pitch: -4 },
  { name: 'rauhankatu', tile: 't0_-5_1', at: [-1180, 318.7], yaw: 58.6, pitch: -4 },
  { name: 'heikkilankatu-detail', tile: 't0_-5_1', top: [-1072, 310], height: 70 },
  { name: 'strips-detail', tile: 't0_-5_1', top: [-1200, 395], height: 70 },
  { name: 'office-lot', tile: 't0_0_0', at: [58, 85], yaw: 146.6, pitch: -6 },
  { name: 'office-kerb', tile: 't0_0_0', at: [8, 148], yaw: 147, pitch: -4 },
  { name: 'office-east-lot', tile: 't0_0_0', at: [190, 132], yaw: 146.6, pitch: -6 },
  { name: 'office-lot-detail', tile: 't0_0_0', top: [90, 100], height: 70 },
  { name: 'office-east-lot-detail', tile: 't0_0_0', top: [212, 165], height: 70 },
];
const views = [
  ...tiles.map(tile => { const [, i, j] = tile.split('_').map(Number); return { name: `${tile}-top`, tile, top: [i * 250 + 125, j * 250 + 125], height: 215 }; }),
  ...spots.filter(s => tiles.includes(s.tile)),
];
const source = `import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { PostProcessing } from './src/render/post';
import { PerformanceMonitor } from './src/render/performance';
import { tintDraft } from './src/render/facadeKit';
import { batchTileShadows } from './src/world/shadowBatch';
import { VegetationLod } from './src/world/vegetationLod';
import { ParkedCarVisuals } from './src/actors/car';
import { slotShown } from './src/world/parkedOccupancy';
const renderer=new THREE.WebGPURenderer({antialias:false,trackTimestamp:true});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1280/720,0.1,2500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-10-28T13:00:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium'),ground=[];
const manifest={vehicles:[],vehicle_models:{},baseUrl:location.origin+'/__parked-sanity/${tiles[0]}/manifest.json'};
for(const tile of ${JSON.stringify(tiles)}){
 const model=(await loader.loadAsync('/__parked-sanity/'+tile+'/vis.glb')).scene;
 model.name=tile;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
 tintDraft(model,renderer);batchTileShadows(model);lod.add(model);scene.add(model);ground.push(model);
 const m=await (await fetch('/__parked-sanity/'+tile+'/manifest.json')).json();
 manifest.vehicles.push(...(m.vehicles??[]).filter(c=>c.model!=='truck'));Object.assign(manifest.vehicle_models,m.vehicle_models??{});
}
if(${legacy})for(const c of manifest.vehicles)c.model={'sedan-sports':'sedan','suv-luxury':'suv'}[c.model]??c.model;
const visuals=new ParkedCarVisuals(scene);await visuals.load(manifest);
const night=${night};
const rest={restingPose(car){if(!slotShown(car,night))return null;const h=(car.yaw-Math.PI/2)/2;
 return {pos:{x:car.x,y:car.y+0.75,z:car.z},rot:{x:0,y:Math.sin(h),z:0,w:Math.cos(h)}};}};
visuals.update([],rest);
const shown=manifest.vehicles.filter(c=>slotShown(c,night)).length;
const raycaster=new THREE.Raycaster();
function groundAt(x,z){raycaster.set(new THREE.Vector3(x,500,z),new THREE.Vector3(0,-1,0));
 const hits=raycaster.intersectObjects(ground,true);return hits.length?hits[hits.length-1].point.y:0;}
// Gap between each shown car's base (its height, pitch and roll) and the drawn ground under its
// corners and centre: positive floats, negative sinks. Every 3rd car keeps the raycasts affordable.
window.audit=()=>{const gaps=[];manifest.vehicles.filter((c,i)=>i%3===0&&slotShown(c,night)).forEach(c=>{
 const fx=Math.sin(c.yaw),fz=Math.cos(c.yaw),rx=-fz,rz=fx,tp=Math.tan(c.pitch??0),tr=Math.tan(c.roll??0);
 for(const [a,b] of [[0,0],[c.length/2,c.width/2],[c.length/2,-c.width/2],[-c.length/2,c.width/2],[-c.length/2,-c.width/2]]){
  const x=c.x+a*fx+b*rx,z=c.z+a*fz+b*rz;gaps.push({id:c.object_id,gap:c.y+a*tp-b*tr-groundAt(x,z)});}});
 const g=gaps.map(v=>v.gap).sort((a,b)=>a-b);
 return {samples:g.length,min:g[0],p50:g[g.length>>1],max:g[g.length-1],floating:new Set(gaps.filter(v=>v.gap>0.15).map(v=>v.id)).size,
  sunk:new Set(gaps.filter(v=>v.gap<-0.15).map(v=>v.id)).size,worst:gaps.sort((a,b)=>Math.abs(b.gap)-Math.abs(a.gap)).slice(0,5)};};
const perf=new PerformanceMonitor(renderer,'webgpu');
window.probe={async view(v){
 if(v.top){const g=groundAt(v.top[0],v.top[1]);camera.fov=60;camera.position.set(v.top[0],g+v.height,v.top[1]+0.01);camera.lookAt(v.top[0],g,v.top[1]);}
 else{const y=groundAt(v.at[0],v.at[1])+1.7,yaw=v.yaw*Math.PI/180,p=v.pitch*Math.PI/180;
  camera.fov=60;camera.position.set(v.at[0],y,v.at[1]);
  camera.lookAt(v.at[0]+Math.sin(yaw)*Math.cos(p)*10,y+Math.sin(p)*10,v.at[1]-Math.cos(yaw)*Math.cos(p)*10);}
 camera.updateProjectionMatrix();camera.updateMatrixWorld();env.follow(camera.position);env.updateIBL();
 for(let i=0;i<40;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 perf.reset();for(let i=0;i<30;i++){perf.begin();lod.update(camera);post.render();perf.end();await new Promise(requestAnimationFrame);}
 await perf.completed();await perf.flush();
 const batches=scene.children.filter(o=>o.isInstancedMesh&&o.name.startsWith('parked/')).map(o=>[o.name,o.count]);
 return {...perf.snapshot(),cars:manifest.vehicles.length,shown,batches};
}};window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile, source, { flag: 'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__parked-sanity.js"></script>', { flag: 'wx' });
  fs.symlinkSync(root, link);
  browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, executablePath: chromeExecutable(), headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']) });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port()}/__parked-sanity.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 240000 });
  const ground = await page.evaluate(() => window.audit());
  console.log('ground gap', JSON.stringify(ground));
  const results = [];
  for (const view of views) {
    const name = `${view.name}-night${night}${legacy ? '-legacy' : ''}`;
    const stats = await page.evaluate(v => window.probe.view(v), view);
    await page.screenshot({ path: path.join(root, `${name}.png`) });
    results.push({ ...view, stats });
    fs.writeFileSync(path.join(root, `render-night${night}${legacy ? '-legacy' : ''}.json`), JSON.stringify({ phase, night, legacy, tiles, ground, errors, results }, null, 2));
    console.log(name, stats.drawCalls, stats.triangles, stats.shown, '/', stats.cars);
  }
  if (errors.length) throw new Error(JSON.stringify(errors));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile, link]) if (fs.existsSync(file)) fs.unlinkSync(file);
}
