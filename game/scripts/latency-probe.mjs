// Manual diagnostic, run through host_slots.py browser. Output stays in the sandbox.
import { chromium } from '@playwright/test';
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
process.env.TMPDIR = process.env.TURKU3D_BROWSER_DIR + '/tmp';
fs.mkdirSync(process.env.TMPDIR, { recursive: true });
const label = process.argv[2] ?? 'before';
const out = `${process.env.TURKU3D_WORK}/reports/latency/${label}`;
fs.mkdirSync(out, { recursive: true });
const port = Number(process.env.TURKU3D_PORT_BASE);
const originals = new Map();
if (label === 'before') {
 for (const file of ['game/src/physics/client.ts','game/src/physics/worker.ts','game/src/play.ts','game/src/input/index.ts','game/index.html']) {
  originals.set(file, fs.readFileSync(file));
  let source=execFileSync('git',['show',`b0a6d655:${file}`],{encoding:'utf8'});
  if(file.endsWith('/worker.ts')) source=source.replace("post({ type: 'state',", "post({ type: 'state', publishedAt: performance.timeOrigin + performance.now(),");
  fs.writeFileSync(file,source);
 }
}
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: [`--remote-debugging-port=${port + 8}`, '--enable-unsafe-webgpu', '--use-angle=metal'] });
try {
const page = await browser.newPage();
await page.addInitScript(() => {
 window.keyCosts = [];
 const add = EventTarget.prototype.addEventListener;
 EventTarget.prototype.addEventListener = function(type, fn, options) {
  if ((type === 'keydown' || type === 'keyup') && typeof fn === 'function') {
   const original = fn;
   fn = function(e) { const t = performance.now(); try { return original.call(this,e); }
    finally { window.keyCosts.push({ type, repeat:e.repeat, ms:performance.now()-t }); } };
  }
  return add.call(this,type,fn,options);
 };
});
page.on('pageerror', e => console.log('PAGE', e.message));
const recorder = spawn(process.execPath, [`${process.env.HOME}/turku3d/profiling/record.mjs`, `http://127.0.0.1:${port+8}`, `http://127.0.0.1:${port}/?scene=dev&test&quality=low&aa=off`, '65', out], { stdio:'inherit' });
await page.waitForFunction(() => window.__game?.ready, null, { timeout:180000 });
await page.getByRole('option', { name:'New game' }).click();
await page.getByRole('button', { name:'Skip', exact:true }).click();
await page.waitForFunction(() => !window.__game.state().paused);
await page.waitForTimeout(3000);
await page.evaluate(() => {
 const p=window.__game.physics; window.latency=[];
 let tick=-1, received=performance.now();
 function sample() { const n=performance.now(), s=p.latest;
 if(s?.tick!==tick){ tick=s?.tick; received=n; }
 window.latency.push({t:n,age:s?.publishedAt ? performance.timeOrigin+n-s.publishedAt : n-received,stepMs:s?.stepMs,tick,transport:p.transport});
 const end=performance.now()+22; while(performance.now()<end){ /* Synthetic main-thread load. */ } requestAnimationFrame(sample); }
 requestAnimationFrame(sample);
});
const cdp=await page.context().newCDPSession(page);
await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'w',code:'KeyW',windowsVirtualKeyCode:87});
const timer=setInterval(() => { void cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'w',code:'KeyW',windowsVirtualKeyCode:87,autoRepeat:true}).catch(()=>{}); },1000/30);
await page.waitForTimeout(45000); clearInterval(timer);
await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'w',code:'KeyW',windowsVirtualKeyCode:87});
const rows=await page.evaluate(()=>window.latency);
const keys=await page.evaluate(()=>window.keyCosts);
fs.writeFileSync(`${out}/key-cost.json`,JSON.stringify(keys));
console.log('key cost',JSON.stringify({events:keys.length,totalMs:keys.reduce((s,k)=>s+k.ms,0),maxMs:Math.max(...keys.map(k=>k.ms))}));
fs.writeFileSync(`${out}/state-age.json`,JSON.stringify(rows));
const perSecond = Object.values(rows.reduce((groups,r) => {
 const second=Math.floor((r.t-rows[0].t)/1000);
 const group=groups[second]??={second,frames:0,maxAge:0,maxStep:0,tickFirst:r.tick,tickLast:r.tick};
 group.frames++; group.maxAge=Math.max(group.maxAge,r.age); group.maxStep=Math.max(group.maxStep,r.stepMs??0); group.tickLast=r.tick;
 return groups;
},{}));
fs.writeFileSync(`${out}/state-seconds.json`,JSON.stringify(perSecond,null,2));
console.log(label,JSON.stringify({frames:rows.length,maxAge:Math.max(...rows.map(r=>r.age)),maxStep:Math.max(...rows.map(r=>r.stepMs??0)),transport:rows.at(-1)?.transport}));
if (label === 'after') {
 const root=`${process.env.TURKU3D_DATA}/live/tiles`;
 const manifest=JSON.parse(fs.readFileSync(`${root}/manifest.json`));
 const pos=manifest.spawn.vehicle.position;
 await page.evaluate(async ({pos,yaw,known}) => {
  const {PhysicsClient}=await import('/src/physics/client.ts');
  const p=new PhysicsClient(); await p.ready; p.setKnown(known,[]);
  window.manoeuvre={p,rows:[],pos,yaw};
 },{pos,yaw:manifest.spawn.vehicle.yaw,known:Object.keys(manifest.collision)});
 for(const [id,tile] of Object.entries(manifest.collision)) {
  const [,i,j]=id.split('_').map(Number);
  if(Math.hypot((i+.5)*62.5-pos[0],(j+.5)*62.5-pos[2])>100) continue;
  const bytes=[...fs.readFileSync(`${root}/${tile.url}`)];
  await page.evaluate(async ({id,bytes}) => { await window.manoeuvre.p.loadTile(id,new Uint8Array(bytes).buffer); },{id,bytes});
 }
 await page.evaluate(() => {
  const m=window.manoeuvre;
  m.p.spawnVehicle({x:m.pos[0],y:m.pos[1]+.75,z:m.pos[2]},m.yaw);
  m.p.setCharacterEnabled(false); m.p.driveVehicle('player');
  const start=performance.now();
  function tick() {
   const t=performance.now()-start;
   m.p.updateInput({vx:0,vz:0,jump:false},{throttle:t>2000?1:0,steer:1,brake:0,handbrake:false},t<12000);
   const s=m.p.latest;
   if(s)m.rows.push({t,stepMs:s.stepMs,substeps:s.substeps,tick:s.tick,speed:s.vehicle?.speed,age:performance.timeOrigin+performance.now()-s.publishedAt});
   if(t<12000)requestAnimationFrame(tick);
  } tick();
 });
 await page.waitForTimeout(12500);
 const manoeuvre=await page.evaluate(()=>window.manoeuvre.rows);
 fs.writeFileSync(`${out}/steered-start-real-tiles.json`,JSON.stringify(manoeuvre));
 console.log('steered start',JSON.stringify({samples:manoeuvre.length,maxStep:Math.max(...manoeuvre.map(r=>r.stepMs)),maxSpeed:Math.max(...manoeuvre.map(r=>r.speed??0))}));
}
if (recorder.exitCode === null) await new Promise(r=>recorder.on('exit',r));
} finally { await browser.close(); for (const [file,content] of originals) fs.writeFileSync(file,content); }
