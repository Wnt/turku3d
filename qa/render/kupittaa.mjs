#!/usr/bin/env node
import { chromeArgs } from '../../tools/dev/chrome-args.mjs';
// Manual Kupittaa performance experiment; no browser test or timing gate.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { isolateBrowser, port, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const drafts = option('--drafts');
if (!drafts) throw new Error('need --drafts DIR containing t0_4_1..t0_6_3 and shared/ textures; see game/src/world/README.md');
const label = option('--label', 'kupittaa');
const out = option('--out', path.join(reportsRoot, 'kupittaa'));
const base = option('--url', `http://127.0.0.1:${port()}`);
const modes = option('--modes', 'baseline,original,fixed').split(',');
if (modes.some(mode => !['baseline', 'original', 'fixed'].includes(mode))) throw new Error('bad --modes');
const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8' }).trim();
const commit = git('rev-parse', 'HEAD'), dirty = !!git('status', '--porcelain');
const generated = [];
let browser;
function write(relative, content) {
  const file = path.join(repo, relative);
  fs.writeFileSync(file, content, { flag: 'wx' }); generated.push(file);
}
const SCENE = `import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { PostProcessing } from './src/render/post';
import { PerformanceMonitor } from './src/render/performance';
import { tintDraft as originalTint } from './src/render/__kupittaaOriginalTint';
import { tintDraft } from './src/render/facadeKit';
import { batchTileShadows as baselineShadows } from './src/world/__kupittaaBaselineShadow';
import { VegetationLod as OriginalLod } from './src/world/__kupittaaOriginal';
import { VegetationLod } from './src/world/vegetationLod';
import { batchTileShadows } from './src/world/shadowBatch';
const renderer = new THREE.WebGPURenderer({ antialias: false, trackTimestamp: true });
await renderer.init(); renderer.setSize(2560,1440); renderer.shadowMap.enabled = true;
document.body.append(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(65,2560/1440,0.1,3500);
camera.position.set(1375,19,625); camera.lookAt(1420,18,450); camera.updateMatrixWorld();
const env = new Environment(scene,renderer,'medium'); env.setTime(new Date('2026-10-25T12:00:00+03:00')); env.follow(camera.position);env.updateIBL();
const post = new PostProcessing(renderer,scene,camera,'medium');
const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const models=[];
for(let i=4;i<=6;i++) for(let j=1;j<=3;j++) {
 const model=(await loader.loadAsync(\`/__kupittaa/t0_\${i}_\${j}/vis.glb\`)).scene;
 model.traverse(o=>{ if(o.isMesh){o.castShadow=true;o.receiveShadow=true;} });
 scene.add(model);models.push(model);

}
const sources=[];
models.forEach(model=>model.traverse(mesh=>{if(mesh.isMesh)sources.push({mesh,material:mesh.material});}));
const tinted=new Set();
const perf = new PerformanceMonitor(renderer,'webgpu');
let update=()=>{};
let lod;
function configure(mode){
 if(lod){models.forEach(m=>lod.remove(m));lod=null;}
 for(const material of tinted)material.dispose();tinted.clear();
 for(const source of sources)source.mesh.material=source.material;
 for(const model of models){
  for(const child of [...model.children]) if(child.userData.shadowBatch){child.removeFromParent();child.geometry.dispose();child.material.dispose();}
  model.traverse(o=>{if(o.isMesh){o.visible=true;o.castShadow=true;o.receiveShadow=true;}});
  (mode==='fixed'?tintDraft:originalTint)(model,renderer);
  model.traverse(mesh=>{if(mesh.isMesh)for(const m of Array.isArray(mesh.material)?mesh.material:[mesh.material])tinted.add(m);});
  (mode==='baseline'?baselineShadows:batchTileShadows)(model);
 }
 if(mode!=='baseline'){lod=new (mode==='original'?OriginalLod:VegetationLod)('medium');models.forEach(m=>lod.add(m));}
 update=()=>lod?.update(camera);
}
window.probe={scene,camera,models,renderer,env,configure,async measure(){
 async function frames(n, measure=false){
  for(let i=0;i<n;i++){if(measure)perf.begin();update();post.render();if(measure)perf.end();await new Promise(requestAnimationFrame);}
 }
 await frames(120);perf.reset();const start=performance.now();await frames(240,true);await perf.completed();await perf.flush();
 return {stats:perf.snapshot(),drainedMsPerFrame:(performance.now()-start)/240};
}};
window.ready=true;
`;

try {
  write('game/src/world/__kupittaaBaselineShadow.ts', git('show', '382fb21c^^:game/src/world/shadowBatch.ts'));
  write('game/src/world/__kupittaaOriginal.ts', git('show', '382fb21c:game/src/world/vegetationLod.ts'));
  write('game/src/render/__kupittaaOriginalTint.ts', git('show', 'b893bb32:game/src/render/facadeKit.ts'));
  write('game/__kupittaa-probe.ts', SCENE);
  write('game/__kupittaa-probe.html', '<link rel="icon" href="data:,"><script type="module" src="/__kupittaa-probe.ts"></script>');
  const link = path.join(repo, 'game/public/__kupittaa');
  fs.symlinkSync(path.resolve(drafts), link); generated.push(link);
  fs.mkdirSync(out, { recursive: true });
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: chromeArgs([
    '--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist',
    '--disable-gpu-vsync', '--disable-frame-rate-limit', '--disable-background-timer-throttling',
  ]) });
  const page = await browser.newPage({ viewport: { width: 2560, height: 1440 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
  await page.goto(`${base}/__kupittaa-probe.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 180000 });
  const results = [];
  for (let repeat = 0; repeat < 3; repeat++) for (const mode of repeat % 2 ? [...modes].reverse() : modes) {
    await page.evaluate(mode => window.probe.configure(mode), mode);
    for (const shadow of [true, false]) {
      const measurement = await page.evaluate(async shadow => {
        window.probe.env.sun.castShadow = shadow;
        return window.probe.measure();
      }, shadow);
      results.push({ repeat, mode, shadow, ...measurement });
      console.log(label, repeat, mode, shadow, JSON.stringify(measurement));
    }
  }
  const files = [];
  for (let i = 4; i <= 6; i++) for (let j = 1; j <= 3; j++) {
    const file = path.join(drafts, `t0_${i}_${j}`, 'vis.glb');
    files.push({ tile: `t0_${i}_${j}`, sha256: createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
  }
  for (const name of fs.existsSync(path.join(drafts, 'shared')) ? fs.readdirSync(path.join(drafts, 'shared')).sort() : []) {
    const file = path.join(drafts, 'shared', name);
    if (fs.statSync(file).isFile()) files.push({ asset: `shared/${name}`, sha256: createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
  }
  const report = { label, date: new Date().toISOString(), commit,
    dirty, baseline: git('rev-parse', '382fb21c^^'), original: git('rev-parse', '382fb21c'),
    browser: browser.version(), cpu: os.cpus()[0]?.model, loadAverage: os.loadavg(),
    camera: { position: [1375, 19, 625], lookAt: [1420, 18, 450], fov: 65 },
    quality: 'medium', draftTint: true, resolution: [2560, 1440], warmFrames: 120, measuredFrames: 240,
    pacing: 'unlocked RAF with end-of-window GPU completion and timestamp drain', files, errors, results };
  fs.writeFileSync(path.join(out, `${label}.json`), JSON.stringify(report, null, 2));
  if (errors.length) throw new Error(JSON.stringify(errors));
  await page.screenshot({ path: path.join(out, `${label}.png`) });
} finally {
  if (browser) await browser.close();
  for (const file of generated.reverse()) fs.unlinkSync(file);
}

