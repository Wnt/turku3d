#!/usr/bin/env node
// Street-richness sanity render (G1 furniture and lights, G2 parked cars, G3 road paint), outside the test gate.
// Usage (from the repo root): node qa/render/street-sanity.mjs OUT NIGHT TILE...  with the game's Vite dev server on
// TURKU3D_PORT_BASE whose /coarse proxy serves the combined tiles. Draws each tile's visual GLB (furniture props,
// register trees, rule road paint), the client's parked-car batches at their draft slots as shown at night
// level NIGHT (0 day, 1 night) and the client's lamp posts from the tile light list, then renders top views of
// every tile and the street views below.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.resolve(process.argv[2]);
const night = Number(process.argv[3] ?? 0);
const tiles = process.argv.slice(4);
fs.mkdirSync(out, { recursive: true });
const sceneFile = 'game/__street-sanity.js', pageFile = 'game/__street-sanity.html';
const spots = [
  { name: 'office-lot-detail', tile: 't0_0_0', top: [90, 100], height: 70 },
  { name: 'office-kerb', tile: 't0_0_0', at: [8, 148], yaw: 147, pitch: -4 },
  { name: 'office-lot', tile: 't0_0_0', at: [58, 85], yaw: 146.6, pitch: -6 },
  { name: 'office-east-lot-detail', tile: 't0_0_0', top: [212, 165], height: 70 },
  { name: 'shopping-detail', tile: 't0_0_-1', top: [17.6, -105.9], height: 70 },
  { name: 'shopping', tile: 't0_0_-1', at: [17.61, -105.9], yaw: 236, pitch: -2 },
  { name: 'heikkilankatu-detail', tile: 't0_-5_1', top: [-1072, 310], height: 70 },
  { name: 'heikkilankatu', tile: 't0_-5_1', at: [-1064.91, 317.01], yaw: 146.7, pitch: -4 },
  { name: 'heikkilankatu-back', tile: 't0_-5_1', at: [-1064.91, 317.01], yaw: 326.7, pitch: -4 },
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
import { tintDraft } from './src/render/facadeKit';
import { batchTileShadows } from './src/world/shadowBatch';
import { VegetationLod } from './src/world/vegetationLod';
import { ParkedCarVisuals } from './src/actors/car';
import { slotShown } from './src/world/parkedOccupancy';
import { LampPosts } from './src/render/lights/lampPosts';
import { decodeTileLights } from './src/render/lights/tileLights';
const renderer=new THREE.WebGPURenderer({antialias:false});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1280/720,0.1,2500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-11-01T13:00:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium'),ground=[],lamps=[];
const manifest={vehicles:[],vehicle_models:{},baseUrl:location.origin+'/coarse/${tiles[0]}/manifest.json'};
const counts={};
for(const tile of ${JSON.stringify(tiles)}){
 const m=await (await fetch('/coarse/'+tile+'/manifest.json')).json();
 const model=(await loader.loadAsync('/coarse/'+tile+'/'+m.visual[tile].url)).scene;
 model.name=tile;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
 tintDraft(model,renderer);batchTileShadows(model);lod.add(model);scene.add(model);ground.push(model);
 manifest.vehicles.push(...(m.vehicles??[]).filter(c=>c.model!=='truck'));Object.assign(manifest.vehicle_models,m.vehicle_models??{});
 const tl=decodeTileLights(m.lights);lamps.push(...tl.filter(l=>l.post));
 counts[tile]={cars:(m.vehicles??[]).length,lights:tl.length,posts:tl.filter(l=>l.post).length,roads:m.roads?.url??null};
}
const visuals=new ParkedCarVisuals(scene);await visuals.load(manifest);
const night=${night};
const rest={restingPose(car){if(!slotShown(car,night))return null;const h=(car.yaw-Math.PI/2)/2;
 return {pos:{x:car.x,y:car.y+0.75,z:car.z},rot:{x:0,y:Math.sin(h),z:0,w:Math.cos(h)}};}};
visuals.update([],rest);
const posts=new LampPosts(4096);posts.setLamps(lamps);posts.night.value=night;scene.add(posts.mesh);
const shown=manifest.vehicles.filter(c=>slotShown(c,night)).length;
const raycaster=new THREE.Raycaster();
function groundAt(x,z){raycaster.set(new THREE.Vector3(x,500,z),new THREE.Vector3(0,-1,0));
 const hits=raycaster.intersectObjects(ground,true);return hits.length?hits[hits.length-1].point.y:0;}
window.probe={async view(v){
 if(v.top){const g=groundAt(v.top[0],v.top[1]);camera.fov=60;camera.position.set(v.top[0],g+v.height,v.top[1]+0.01);camera.lookAt(v.top[0],g,v.top[1]);}
 else{const y=groundAt(v.at[0],v.at[1])+(v.rise??1.7),yaw=v.yaw*Math.PI/180,p=v.pitch*Math.PI/180;
  camera.fov=60;camera.position.set(v.at[0],y,v.at[1]);
  camera.lookAt(v.at[0]+Math.sin(yaw)*Math.cos(p)*10,y+Math.sin(p)*10,v.at[1]-Math.cos(yaw)*Math.cos(p)*10);}
 camera.updateProjectionMatrix();camera.updateMatrixWorld();env.follow(camera.position);env.updateIBL();
 posts.update(camera.position.x,camera.position.z,performance.now()+1e6);
 for(let i=0;i<40;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 return {cars:manifest.vehicles.length,shown,posts:posts.mesh.count};
}};window.counts=counts;window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile, source, { flag: 'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__street-sanity.js"></script>', { flag: 'wx' });
  browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, executablePath: chromeExecutable(), headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']) });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port()}/__street-sanity.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 240000 });
  const counts = await page.evaluate(() => window.counts);
  console.log('tiles', JSON.stringify(counts));
  const results = [];
  for (const view of views) {
    const name = `${view.name}-night${night}`;
    const stats = await page.evaluate(v => window.probe.view(v), view);
    await page.screenshot({ path: path.join(out, `${name}.png`) });
    results.push({ ...view, stats });
    console.log(name, JSON.stringify(stats));
  }
  fs.writeFileSync(path.join(out, `combined-sanity-night${night}.json`), JSON.stringify({ night, tiles, counts, errors, results }, null, 2));
  if (errors.length) console.log('errors', JSON.stringify(errors.slice(0, 10)));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile]) if (fs.existsSync(file)) fs.unlinkSync(file);
}
