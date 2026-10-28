#!/usr/bin/env node
import { chromeArgs, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
// Manual real-data generator inspection, deliberately outside the test gate.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const phase = process.argv[2];
if (!['before', 'after'].includes(phase)) throw new Error('usage: node qa/render/bridge-sanity.mjs before|after');
const root = path.join(workRoot, 'reports/bridge-sanity', phase);
const region = process.argv[3] ?? 'aura';
const tiles = fs.readdirSync(root).filter(t => /^t0_/.test(t) && fs.existsSync(path.join(root, t, 'manifest.json')) && (region === 'hame' ? Number(t.split('_')[1]) >= 4 : Number(t.split('_')[1]) <= 0));
const sceneFile = 'game/__bridge-sanity.js', pageFile = 'game/__bridge-sanity.html';
const link = 'game/public/__bridge-sanity';
const spots = [
  { name: '01-teatterisilta', target: [3, 4, 394], radius: 100 },
  { name: '02-theatre-boats', target: [-39, 3, 445], radius: 85 },
  { name: '03-riverbank', target: [-134, 3, 483], radius: 90 },
  { name: '04-myllysilta', target: [-351, 5, 589], radius: 115 },
  { name: '05-boat-slabs', target: [-451, 3, 649], radius: 115 },
  { name: '06-betanianpolku', target: [-343, 12, 915], radius: 105 },
];
const views = region === 'hame' ? [{ name: '07-hameensilta', target: [1483, 16, -506], radius: 145 }] : [...spots];
for (const tile of tiles) {
  const [, i, j] = tile.split('_').map(Number);
  views.push({ name: tile, target: [i * 250 + 125, 0, j * 250 + 125], radius: 230 });
}
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
const renderer=new THREE.WebGPURenderer({antialias:false,trackTimestamp:true});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(55,1280/720,0.1,2500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-10-28T12:00:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium'),models=[];
for(const tile of ${JSON.stringify(tiles)}){
 const model=(await loader.loadAsync('/__bridge-sanity/'+tile+'/vis.glb')).scene;
 model.name=tile;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
 tintDraft(model,renderer);batchTileShadows(model);lod.add(model);scene.add(model);models.push(model);
}
const perf=new PerformanceMonitor(renderer,'webgpu');
window.probe={async view(v){
 const t=new THREE.Vector3(...v.target),r=v.radius;
 camera.position.copy(t).add(v.top?new THREE.Vector3(0,r*2.2,0.01):new THREE.Vector3(-r*0.6,r*0.85,r*1.4));
 camera.lookAt(t);camera.updateMatrixWorld();env.follow(camera.position);env.updateIBL();
 // Compare identical successful baseline tiles: 11 Aura tiles and 7 Häme tiles.
 for(const model of models)model.visible=!(v.name.startsWith('0')&&['t0_-2_1','t0_4_-3','t0_4_-2'].includes(model.name));
 for(let i=0;i<40;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 perf.reset();for(let i=0;i<60;i++){perf.begin();lod.update(camera);post.render();perf.end();await new Promise(requestAnimationFrame);}
 await perf.completed();await perf.flush();return perf.snapshot();
}};window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile, source, { flag: 'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__bridge-sanity.js"></script>', { flag: 'wx' });
  fs.symlinkSync(root, link);
  browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, channel: 'chrome', headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']) });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port()}/__bridge-sanity.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 180000 });
  const results = [];
  for (const view of views) for (const top of [true, false]) {
    const name = `${view.name}-${top ? 'top' : 'oblique'}`;
    const stats = await page.evaluate(v => window.probe.view(v), { ...view, top });
    await page.screenshot({ path: path.join(root, `${name}.png`) });
    results.push({ ...view, top, stats });
    fs.writeFileSync(path.join(root, `render-${region}.json`), JSON.stringify({ phase, tiles, errors, results }, null, 2));
    console.log(name, stats.drawCalls, stats.triangles);
  }
  if (errors.length) throw new Error(JSON.stringify(errors));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile, link]) if (fs.existsSync(file)) fs.unlinkSync(file);
}
