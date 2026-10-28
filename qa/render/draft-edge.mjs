#!/usr/bin/env node
// Manual real-data latency proof, outside the gate: production draft streamer + Rapier worker.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { chromeArgs, chromeExecutable, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, port, workRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(workRoot, 'reports/draft-speed');
const sceneFile = 'game/__draft-edge.js', pageFile = 'game/__draft-edge.html';
const published = 'game/public/__draft-published';
let browser, page;
try {
  fs.symlinkSync(fs.realpathSync(path.join(process.env.TURKU3D_DATA, 'live/tiles')), published);
  fs.writeFileSync(sceneFile, `import * as THREE from 'three/webgpu';
import { CoarseTiles } from './src/world/coarseTiles';
import { CollisionStreamer } from './src/world/collisionStreaming';
import { PhysicsClient } from './src/physics/client';
const renderer = new THREE.WebGPURenderer({antialias:true});
await renderer.init();renderer.setSize(1280,720);document.body.style.margin='0';document.body.append(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color('#a3c7dd');
scene.add(new THREE.HemisphereLight(0xffffff,0x56533f,3));
const sun=new THREE.DirectionalLight(0xffffff,3);sun.position.set(-100,200,100);scene.add(sun);
const camera=new THREE.PerspectiveCamera(55,1280/720,0.1,2000);
const manifest=await fetch('/__draft-published/manifest.json').then(r=>r.json());
manifest.baseUrl=new URL('/__draft-published/',location.href).href;
const core=new Set(['t0_0_0','t0_0_1','t0_1_0','t0_1_1']);
manifest.visual=Object.fromEntries(Object.entries(manifest.visual).filter(([id])=>core.has(id)));
manifest.collision=Object.fromEntries(Object.entries(manifest.collision).filter(([id])=>{const [,i,j]=id.split('_').map(Number);return i>=0&&i<8&&j>=0&&j<8;}));
const physics=new PhysicsClient();await physics.ready;
const streamer=new CollisionStreamer(manifest,physics);
const rows={},events=[],errors=[];physics.onError=e=>errors.push(e);
let started;
const fetcher=async (url,options)=>{
 const at=performance.now(),r=await fetch(url,options);
 const match=String(url).match(/(t0_-?\\d+_-?\\d+)\\/manifest/);
 if(started&&match){const id=match[1];rows[id]??={request:(at-started)/1000};
  if(r.status===200){const m=await r.clone().json();events.push({id,pass:m.pass_kind,at:(performance.now()-started)/1000});}}
 return r;
};
const coarse=new CoarseTiles({scene,renderer,manifest,manifestUrl:'/__draft-published/manifest.json',tiles:new THREE.EventDispatcher(),
 checkCollision:b=>physics.checkTile(b),refreshCollision:ids=>streamer.refresh(ids),unloadCollision:id=>physics.unloadTile(id),fetch:fetcher});
const start={x:349,z:284},velocity={x:8.34,z:12.47};
streamer.update(start.x,start.z,true);
window.probe={physics,streamer,coarse,rows,events,errors,start,velocity,begin(){
 started=performance.now();coarse.velocity={...velocity};
 renderer.setAnimationLoop(()=>{
  const elapsed=(performance.now()-started)/1000,x=start.x+velocity.x*elapsed,z=start.z+velocity.z*elapsed;
  coarse.update(x,z);streamer.update(x,z,false,velocity.x,velocity.z);
  const edges=streamer.trueEdges();
  for(const [id,cell,edge] of [['t0_1_2','c_7_8',[7,7,2]],['t0_2_2','c_8_8',[7,8,0]]]){
   const e=coarse.entries.get(id),r=rows[id];
   if(r&&r.open===undefined&&e?.state==='draft'&&physics.loaded.has(cell)&&!edges.some(v=>v.every((n,k)=>n===edge[k])))r.open=elapsed;
  }
  camera.position.set(x-40,55,z-50);camera.lookAt(x+40,20,z+50);renderer.render(scene,camera);
 });
}};window.ready=true;`, { flag:'wx' });
  fs.writeFileSync(pageFile, '<link rel="icon" href="data:,"><script type="module" src="/__draft-edge.js"></script>', { flag:'wx' });
  browser = await chromium.launch({ executablePath:chromeExecutable(), ignoreDefaultArgs:chromeIgnoreDefaultArgs,
    headless:true, args:chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
  page = await browser.newPage({ viewport:{ width:1280, height:720 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port()}/__draft-edge.html?run=${Date.now()}`);
  await page.waitForFunction(() => window.ready, null, { timeout:120000 });
  await page.waitForFunction(() => { window.probe.streamer.update(349,284); return window.probe.physics.loaded.has('c_7_7'); }, null, { timeout:30000 });
  await page.evaluate(() => window.probe.begin());
  await page.waitForFunction(() => window.probe.rows.t0_1_2?.open !== undefined && window.probe.rows.t0_2_2?.open !== undefined, null, { timeout:90000 });
  await page.screenshot({ path:path.join(out, 'kaskenkatu-edge-open.png') });
  const result = await page.evaluate(() => {
    const p=window.probe;
    return { route:'Kaskenkatu southeast/eastbound', start:p.start, velocity:p.velocity, speed:Math.hypot(p.velocity.x,p.velocity.z),
      expectedArrival:{ t0_1_2:(500-p.start.z)/p.velocity.z,t0_2_2:(500-p.start.x)/p.velocity.x },
      rows:p.rows,events:p.events,physicsErrors:p.errors,edges:p.streamer.trueEdges(),loaded:[...p.physics.loaded.keys()] };
  });
  fs.writeFileSync(path.join(out, 'kaskenkatu-edge.json'), JSON.stringify({ ...result,errors },null,2));
  if (errors.length || result.physicsErrors.length) throw new Error(JSON.stringify({ errors,physics:result.physicsErrors }));
  console.log(JSON.stringify(result.rows));
} catch (error) {
  const diagnostic = await page?.evaluate(() => {
    const p = window.probe;
    return p && { rows:p.rows,events:p.events,errors:p.errors,velocity:p.coarse.velocity,
      entries:[...p.coarse.entries].map(([id,e])=>({id,state:e.state,error:e.error,drawn:!!e.model?.parent})),
      loaded:[...p.physics.loaded.keys()],edges:p.streamer.trueEdges() };
  }).catch(() => null);
  fs.writeFileSync(path.join(out, 'kaskenkatu-edge-failed.json'),JSON.stringify({error:String(error),diagnostic},null,2));
  console.log(JSON.stringify(diagnostic));
  throw error;
} finally {
  await browser?.close();
  for (const p of [sceneFile,pageFile]) fs.rmSync(p,{force:true});
  if (fs.existsSync(published)) fs.unlinkSync(published);
}
