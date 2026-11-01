#!/usr/bin/env node
// Manual real-data inspection of roads.json v2 lanes and road paint (phase G3), outside the test gate.
//   node qa/render/road-paint.mjs ROOT OUT [tile ...]
// ROOT holds built draft tiles (<tile>/vis.glb, <tile>/roads.json, shared/); OUT receives PNGs and views.json.
// Needs the Vite dev server of this checkout on TURKU3D_PORT_BASE (npm --prefix game run dev).
// Views come from each tile's roads.json: a top-down overview, and top-down zooms plus street-level oblique views
// at a rule zebra, a signal stop line, a centre line, a seam-crossing edge and a bridge deck.
// ROAD_PAINT_VIEWS=zebra,bridge limits the views to names containing one of the words.
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [root, out] = process.argv.slice(2, 4).map(p => path.resolve(p));
if (!root || !out) throw new Error('usage: node qa/render/road-paint.mjs ROOT OUT [tile ...]');
const all = fs.readdirSync(root).filter(t => /^t0_/.test(t) && fs.existsSync(path.join(root, t, 'vis.glb')));
const tiles = process.argv.length > 4 ? process.argv.slice(4) : all;
fs.mkdirSync(out, { recursive: true });

const EDGE = { BRIDGE: 4, START_JUNCTION: 512, END_JUNCTION: 1024, SMOOTH: 32768 };
const SIGNAL = 1, CROSSING = 2, MARKED = 64, EXPLICIT = 256;

function edges(graph) {
  const col = Object.fromEntries(graph.edgeAttrColumns.map((n, i) => [n, i]));
  return graph.edges.map((row, i) => {
    const pts = [[graph.nodes[2 * row[0]], graph.nodes[2 * row[0] + 1]]];
    for (let k = 4; k < row.length; k += 2) pts.push([row[k], row[k + 1]]);
    pts.push([graph.nodes[2 * row[1]], graph.nodes[2 * row[1] + 1]]);
    const cum = [0];
    for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
    const attrs = Object.fromEntries(Object.entries(col).map(([n, j]) => [n, graph.edgeAttrs[i][j]]));
    const flat = graph.edgeEvents[i] ?? [];
    const events = [];
    for (let k = 0; k < flat.length; k += 3) events.push({ s: flat[k], code: flat[k + 1] });
    return { flags: row[2], pts, cum, ys: graph.edgeHeights?.[i] ?? null, attrs, events, length: cum[cum.length - 1] };
  });
}

function at(edge, s) {
  const { pts, cum, ys } = edge;
  for (let k = 1; k < pts.length; k++) {
    if (cum[k] >= s || k === pts.length - 1) {
      const span = cum[k] - cum[k - 1] || 1, t = Math.min(Math.max((s - cum[k - 1]) / span, 0), 1);
      const x = pts[k - 1][0] + t * (pts[k][0] - pts[k - 1][0]), z = pts[k - 1][1] + t * (pts[k][1] - pts[k - 1][1]);
      const y = ys ? ys[k - 1] + t * (ys[k] - ys[k - 1]) : 0;
      return { x, y, z, dx: (pts[k][0] - pts[k - 1][0]) / span, dz: (pts[k][1] - pts[k - 1][1]) / span };
    }
  }
}

// Street level: eye 3.2 m up, `back` metres behind the target along -heading, `side` metres to the right.
function street(name, tile, p, sense = 1, back = 16, side = 1.6) {
  const hx = p.dx * sense, hz = p.dz * sense, rx = -hz, rz = hx;
  return { name, tile, target: [p.x, p.y + 0.5, p.z], position: [p.x - hx * back + rx * side, p.y + 3.2, p.z - hz * back + rz * side], fov: 60 };
}

// Top-down: a narrow field of view from high up keeps buildings from hiding the street.
function top(name, tile, x, y, z, extent, size = 1600) {
  const height = Math.max(120, extent * 3);
  // A near plane close to the ground keeps 8 mm paint above the asphalt in a 24-bit depth buffer.
  return { name, tile, target: [x, y, z], position: [x, y + height, z + 0.01], near: height - 60,
    fov: 2 * Math.atan(extent / 2 / height) * 180 / Math.PI, width: size, height: size };
}

const views = [];
const only = process.env.ROAD_PAINT_VIEWS?.split(',');
for (const tile of tiles) {
  const graph = JSON.parse(fs.readFileSync(path.join(root, tile, 'roads.json'), 'utf8'));
  const [x0, z0, x1, z1] = graph.bbox, cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
  const list = edges(graph), car = list.filter(e => e.flags & 2);
  const yMid = car.find(e => e.ys)?.ys[0] ?? 5;
  views.push(top(`${tile}-top`, tile, cx, yMid, cz, 262, 2048));
  const pick = (test) => { for (const e of car) for (const ev of e.events) if (test(e, ev)) return { e, ev }; return null; };
  const zebra = pick((e, ev) => (ev.code & 15) === CROSSING && ev.code & MARKED && !(ev.code & EXPLICIT) && !(ev.code & 512));
  if (zebra) {
    const p = at(zebra.e, zebra.ev.s);
    views.push(top(`${tile}-zebra-top`, tile, p.x, p.y, p.z, 40));
    views.push(street(`${tile}-zebra`, tile, p, 1, 14, 0.5));
  }
  const stop = pick((e, ev) => (ev.code & 15) === SIGNAL && (e.attrs.flags & EDGE.SMOOTH));
  if (stop) {
    const p = at(stop.e, stop.ev.s);
    views.push(top(`${tile}-stop-top`, tile, p.x, p.y, p.z, 40));
    views.push(street(`${tile}-stopline`, tile, p, stop.ev.code & 16 ? 1 : -1));
  }
  const centre = car.filter(e => e.attrs.lanes_bwd > 0 && e.attrs.flags & EDGE.SMOOTH && e.length > 40).sort((p, q) => q.length - p.length)[0];
  if (centre) views.push(street(`${tile}-centre`, tile, at(centre, centre.length / 2), 1, 22, 1.6));
  const lined = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary'];
  const seam = car.find(e => e.attrs.s0 > 0 && e.attrs.lanes_bwd > 0 && e.length > 8 && e.attrs.flags & EDGE.SMOOTH
    && lined.includes(graph.bits.highways[e.attrs.highway]));
  if (seam) {
    const p = at(seam, 0);
    views.push(top(`${tile}-seam-top`, tile, p.x, p.y, p.z, 40));
    views.push(street(`${tile}-seam`, tile, at(seam, 2), 1, 12, 1.6));
  }
  const bridge = car.filter(e => e.attrs.flags & EDGE.BRIDGE).sort((p, q) => q.length - p.length)[0];
  if (bridge) {
    const p = at(bridge, bridge.length / 2);
    views.push(top(`${tile}-bridge-top`, tile, p.x, p.y, p.z, 60));
    views.push(street(`${tile}-bridge`, tile, p, 1, 20, 1.6));
    views.push(street(`${tile}-bridge-back`, tile, p, -1, 20, 1.6));
  }
}
if (only) views.splice(0, views.length, ...views.filter(v => only.some(o => v.name.includes(o))));
fs.writeFileSync(path.join(out, 'views.json'), JSON.stringify(views, null, 1));

const sceneFile = 'game/__road-paint.js', pageFile = 'game/__road-paint.html', link = 'game/public/__road-paint';
const source = `import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { Environment } from './src/render/environment';
import { PostProcessing } from './src/render/post';
import { tintDraft } from './src/render/facadeKit';
import { batchTileShadows } from './src/world/shadowBatch';
import { VegetationLod } from './src/world/vegetationLod';
const renderer=new THREE.WebGPURenderer({antialias:false});
await renderer.init();renderer.setSize(1280,720);renderer.shadowMap.enabled=true;
document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1280/720,0.1,2500);
const env=new Environment(scene,renderer,'medium');env.setTime(new Date('2026-10-28T12:30:00+02:00'));
const post=new PostProcessing(renderer,scene,camera,'medium');
const loader=new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).setKTX2Loader(new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer));
const lod=new VegetationLod('medium');let paint=0;
for(const tile of ${JSON.stringify(all)}){
 const model=(await loader.loadAsync('/__road-paint/'+tile+'/vis.glb')).scene;
 model.traverse(o=>{if(o.isMesh&&o.userData.layer==='markings')paint+=o.geometry.index.count/3;});
 tintDraft(model,renderer);batchTileShadows(model);lod.add(model);scene.add(model);
}
window.probe={async view(v){
 camera.position.set(...v.position);camera.near=v.near??0.1;camera.fov=v.fov??60;camera.aspect=(v.width??1280)/(v.height??720);camera.updateProjectionMatrix();
 renderer.setSize(v.width??1280,v.height??720);camera.lookAt(new THREE.Vector3(...v.target));camera.updateMatrixWorld();
 env.follow(camera.position);env.updateIBL();
 for(let i=0;i<40;i++){lod.update(camera);post.render();await new Promise(requestAnimationFrame);}
 return {paintTriangles:paint};
}};window.ready=true;`;
let browser;
try {
  fs.writeFileSync(sceneFile, source, { flag: 'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__road-paint.js"></script>', { flag: 'wx' });
  fs.symlinkSync(root, link);
  browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, executablePath: chromeExecutable(), headless: true,
    args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling']) });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port()}/__road-paint.html`);
  await page.waitForFunction(() => window.ready, {}, { timeout: 300000 });
  for (const view of views) {
    await page.setViewportSize({ width: view.width ?? 1280, height: view.height ?? 720 });
    const stats = await page.evaluate(v => window.probe.view(v), view);
    await page.screenshot({ path: path.join(out, `${view.name}.png`) });
    console.log(view.name, JSON.stringify(stats));
  }
  if (errors.length) console.error(JSON.stringify(errors.slice(0, 20)));
} finally {
  await browser?.close();
  for (const file of [sceneFile, pageFile, link]) if (fs.lstatSync(file, { throwIfNoEntry: false })) fs.unlinkSync(file);
}
