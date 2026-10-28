#!/usr/bin/env node
// Manual real-data generator inspection, deliberately outside the test gate.
import { chromium } from 'playwright';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const phase = process.argv[2];
const baseline = process.argv[3] ?? '312849f6';
const full = process.argv.includes('--full');
if (!['before', 'after'].includes(phase)) throw new Error('usage: node qa/render/texture-sanity.mjs before|after');
const root = path.join(workRoot, 'reports/texture-sanity', phase);
const region = full ? 'full' : 'textures';
const tiles = full ? ['t0_0_0', 't0_0_1', 't0_1_0'] : fs.readdirSync(root).filter(t => /^t0_/.test(t) && fs.existsSync(path.join(root, t, 'manifest.json')));
const sceneFile = 'game/__texture-sanity.js', pageFile = 'game/__texture-sanity.html';
const link = 'game/public/__texture-sanity';
const views = JSON.parse(fs.readFileSync(path.join(workRoot, 'reports/texture-sanity/views.json'), 'utf8')).filter(v => tiles.includes(v.tile));
const snapshot = 'game/__texture-before-facade.ts';
const materialSnapshot = 'game/src/render/ground/__texture-before-material.ts';
const bindingSnapshot = 'game/src/render/ground/__texture-before-binding.ts';
const fullLink = 'game/public/__texture-full';
const source = `import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { PostProcessing } from './src/render/post';
import { PerformanceMonitor } from './src/render/performance';
import { tintDraft } from '${phase === 'before' ? './__texture-before-facade' : './src/render/facadeKit'}';
import { batchTileShadows } from './src/world/shadowBatch';
import { VegetationLod } from './src/world/vegetationLod';
import { MaterialStyler } from './src/render/materials';
import { GroundBindings } from '${phase === 'before' ? './src/render/ground/__texture-before-binding' : './src/render/ground/binding'}';
const renderer=new THREE.WebGPURenderer({antialias:false,trackTimestamp:true});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(55,1280/720,0.1,2500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-10-28T12:00:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium'),models=[];
const styler=new MaterialStyler(renderer,env,'medium',true);
const ground=new GroundBindings(renderer,new URL('/__texture-full/tileset.json',location.href).href);
for(const tile of ${JSON.stringify(tiles)}){
 const model=(await loader.loadAsync(${full} ? '/__texture-full/visual/0/'+tile+'.glb' : '/__texture-sanity/'+tile+'/vis.glb')).scene;
 model.name=tile;model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
 if(${full}){styler.apply(model);await ground.apply(model);}else{tintDraft(model,renderer);}batchTileShadows(model);lod.add(model);scene.add(model);models.push(model);
}
const perf=new PerformanceMonitor(renderer,'webgpu');
window.probe={async view(v){
 const t=new THREE.Vector3(...v.target),r=v.radius ?? 100;
 camera.position.copy(v.position ? new THREE.Vector3(...v.position) : t.clone().add(v.top?new THREE.Vector3(0,r*2.2,0.01):new THREE.Vector3(-r*0.6,r*0.85,r*1.4)));
 camera.fov=v.fov??55;camera.aspect=(v.width??1280)/(v.height??720);camera.updateProjectionMatrix();renderer.setSize(v.width??1280,v.height??720);
 camera.lookAt(t);camera.updateMatrixWorld();env.follow(camera.position);env.updateIBL();
 // Isolate each tile so before/after counts use identical visibility.
 for(const model of models)model.visible=model.name===v.tile;
 for(let i=0;i<40;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 perf.reset();for(let i=0;i<60;i++){perf.begin();lod.update(camera);post.render();perf.end();await new Promise(requestAnimationFrame);}
 await perf.completed();await perf.flush();return perf.snapshot();
}};window.ready=true;`;
let browser;
try {
  if (phase === 'before') {
    fs.writeFileSync(snapshot, execFileSync('git', ['show', `${baseline}:game/src/render/facadeKit.ts`]));
    fs.writeFileSync(materialSnapshot, execFileSync('git', ['show', `${baseline}:game/src/render/ground/material.ts`]));
    fs.writeFileSync(bindingSnapshot, fs.readFileSync('game/src/render/ground/binding.ts', 'utf8').replace("from './material'", "from './__texture-before-material'"));
  }
  if (full) {
    const frozen = path.join(workRoot, 'reports/texture-sanity/full-source.json');
    if (!fs.existsSync(frozen)) fs.writeFileSync(frozen, JSON.stringify({ root: fs.realpathSync(path.join(process.env.TURKU3D_DATA, 'live/tiles')) }));
    fs.symlinkSync(JSON.parse(fs.readFileSync(frozen, 'utf8')).root, fullLink);
  }
  fs.writeFileSync(sceneFile, source, { flag: 'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__texture-sanity.js"></script>', { flag: 'wx' });
  fs.symlinkSync(root, link);
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port()}/__texture-sanity.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 180000 });
  const results = [];
  for (const view of views) {
    const top = view.top ?? false;
    const name = (full ? 'full-' : '') + view.name;
    await page.setViewportSize({ width: view.width ?? 1280, height: view.height ?? 720 });
    const stats = await page.evaluate(v => window.probe.view(v), { ...view, top });
    await page.screenshot({ path: path.join(root, `${name}.png`) });
    results.push({ ...view, top, stats });
    fs.writeFileSync(path.join(root, `render-${region}.json`), JSON.stringify({ phase, tiles, errors, results }, null, 2));
    console.log(name, stats.drawCalls, stats.triangles);
  }
  if (errors.length) throw new Error(JSON.stringify(errors));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile, link, snapshot, materialSnapshot, bindingSnapshot, fullLink]) if (fs.existsSync(file)) fs.unlinkSync(file);
}
