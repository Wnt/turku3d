#!/usr/bin/env node
import { chromeArgs } from '../../tools/dev/chrome-args.mjs';
// Manual real-tile roof inspection; no external map imagery or browser test added.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const root = path.join(workRoot, 'reports/roofs');
const dataRoot = process.env.TURKU3D_DATA ?? path.join(process.env.HOME, 'turku3d/data');
const tiles = ['t0_-1_1', 't0_-2_2', 't0_-2_3', 't0_0_0'];
const views = [];
for (const tile of tiles) {
  const [, i, j] = tile.split('_').map(Number);
  views.push({ name:tile, target:[i * 250 + 125, 15, j * 250 + 125], radius:170 });
  const findings = JSON.parse(fs.readFileSync(path.join(dataRoot, 'research/pipeline-problems', `${tile}.json`), 'utf8'));
  for (const [index, f] of findings.entries()) views.push({ name:`${tile}-finding-${index + 1}`, target:[f.position.E - 23459500, f.kind === 'roof height' ? f.our_value : 15, 6704500 - f.position.N], radius:42 });
}
const sceneFile = 'game/__roof-sanity.js', pageFile = 'game/__roof-sanity.html', link = 'game/public/__roof-sanity';
const source = `import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { facadeKitMaterial } from './src/render/facadeKit';
const renderer=new THREE.WebGPURenderer({forceWebGL:true,antialias:true});
await renderer.init();renderer.setSize(1100,850);renderer.setClearColor(0xabb6bb);
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-150,150,116,-116,0.1,2500);
const environment=new Environment(scene,renderer,'low');environment.setTime(new Date('2026-10-28T12:00:00+02:00'));
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const groups={};
for(const phase of ['before','after']){
 const group=new THREE.Group();groups[phase]=group;scene.add(group);
 for(const tile of ${JSON.stringify(tiles)}){const model=(await loader.loadAsync('/__roof-sanity/'+phase+'/'+tile+'/vis.glb')).scene;model.name=tile;model.traverse(o=>{if(o.isMesh){const style=m=>facadeKitMaterial(m,renderer)??m;o.material=Array.isArray(o.material)?o.material.map(style):style(o.material);}});group.add(model);}
}
window.probe={async view(v){
 for(const phase of ['before','after']){groups[phase].visible=phase===v.phase;for(const model of groups[phase].children)model.visible=model.name===v.name.split('-finding-')[0];}
 const t=new THREE.Vector3(...v.target),r=v.radius;
 camera.left=-r*1100/850;camera.right=r*1100/850;camera.top=r;camera.bottom=-r;camera.updateProjectionMatrix();
 camera.position.copy(t).add(v.top?new THREE.Vector3(0,600,0.01):new THREE.Vector3(-r*1.5,r*1.5,r*2));camera.lookAt(t);camera.updateMatrixWorld();environment.follow(camera.position);environment.updateIBL();
 for(let i=0;i<4;i++){renderer.render(scene,camera);await new Promise(requestAnimationFrame);}
 return renderer.info.render;
}};window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile, source, { flag:'wx' });
  fs.writeFileSync(pageFile, '<script type="module" src="/__roof-sanity.js"></script>', { flag:'wx' });
  fs.symlinkSync(root, link);
  browser = await chromium.launch({ channel:'chrome', headless:true, args:chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
  const page = await browser.newPage({ viewport:{ width:1100, height:850 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  await page.goto(`http://127.0.0.1:${port()}/__roof-sanity.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout:240000 });
  const results = [];
  for (const view of views) for (const top of [true, false]) for (const phase of ['before', 'after']) {
    const stats = await page.evaluate(v => window.probe.view(v), { ...view, top, phase });
    const name = `${view.name}-${top ? 'top' : 'oblique'}`;
    await page.screenshot({ path:path.join(root, phase, `${name}.png`) });
    results.push({ ...view, phase, top, stats });
    console.log(phase, name);
  }
  fs.writeFileSync(path.join(root, 'render.json'), JSON.stringify({ tiles, errors, results }, null, 2));
  const panels = views.flatMap(view => [true, false].map(top => {
    const name = `${view.name}-${top ? 'top' : 'oblique'}`;
    return `<section><h2>${name}</h2><div><figure><figcaption>Before</figcaption><img src="before/${name}.png"></figure><figure><figcaption>After</figcaption><img src="after/${name}.png"></figure></div></section>`;
  }));
  fs.writeFileSync(path.join(root, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Roof sanity comparison</title><style>body{font:16px system-ui;background:#202326;color:white}section div{display:flex}figure{margin:4px;width:49%}img{width:100%}</style><h1>Roof sanity comparison</h1>${panels.join('')}`);
  if (errors.length) throw new Error(JSON.stringify(errors));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile, link]) if (fs.existsSync(file)) fs.unlinkSync(file);
}
