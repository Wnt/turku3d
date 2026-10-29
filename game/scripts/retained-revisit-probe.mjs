// Revisit the office after leaving residency; the after run seeds the same owned browser cache across deploy.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import WebSocket from '../../server/node_modules/ws/wrapper.mjs';
import { createServer } from '../../server/http.js';
import { ScoreStore } from '../../server/store.js';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [label = 'before', seed] = process.argv.slice(2);
const out = path.join(reportsRoot, 'retained-revisit', label);
const profile = path.join(process.env.TURKU3D_BROWSER_DIR, 'retained-revisit');
fs.mkdirSync(out, { recursive:true });
if (seed && !fs.existsSync(profile)) fs.cpSync(seed, profile, { recursive:true });
const room = createServer({store:new ScoreStore(path.join(out, 'scores'))});
await new Promise(resolve => room.server.listen(port(4), '127.0.0.1', resolve));
const context = await chromium.launchPersistentContext(profile, {
  executablePath:chromeExecutable(), ignoreDefaultArgs:chromeIgnoreDefaultArgs, headless:true,
  viewport:{width:1280,height:720}, args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist']),
});
const hardStop = setTimeout(() => { void context.close(); }, 240000);
const result = {label, errors:[], requests:[], started:new Date().toISOString()};
try {
  const page = await context.newPage();
  page.on('pageerror', error => result.errors.push(error.message));
  await page.routeWebSocket('wss://arrival.invalid/', socket => {
    const upstream = new WebSocket(`ws://127.0.0.1:${port(4)}/`, { origin:'https://wnt.github.io' });
    const queued=[];
    socket.onMessage(message => { if(upstream.readyState===WebSocket.OPEN) upstream.send(message); else queued.push(message); });
    upstream.on('open',()=>{for(const message of queued)upstream.send(message);queued.length=0;});
    upstream.on('message',(data,binary)=>socket.send(binary ? data : data.toString()));
    upstream.on('error',()=>socket.close()); upstream.on('close',()=>socket.close()); socket.onClose(()=>upstream.close());
  });
  await page.route('https://arrival.invalid/**',async route=>{
    const url=new URL(route.request().url());
    const response=await route.fetch({url:`http://127.0.0.1:${port(4)}${url.pathname}${url.search}`});
    await route.fulfill({response});
  });
  page.on('response',async response=>{
    if(!/\/coarse\/t0_0_0\/(collision|manifest)\.json/.test(response.url()))return;
    const body=await response.json().catch(()=>({}));
    result.requests.push({at:Date.now(),status:response.status(),pass:body.pass_kind,
      stale:!!body.stale,collision_pending:!!body.collision_pending,cells:Object.keys(body.collision??{}).length,
      visual:body.visual?.t0_0_0?.sha256});
  });
  await page.addInitScript(()=>{localStorage.clear();sessionStorage.clear();});
  const entered=Date.now();
  await page.goto('https://wnt.github.io/turku-game/game/?mp=wss%3A%2F%2Farrival.invalid%2F',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.__game?.ready,null,{timeout:120000});
  await page.getByText('Multiplayer',{exact:true}).click();
  await page.waitForFunction(()=>{const e=window.__game.coarse.entries.get('t0_0_0');return e?.model&&e.pass==='full';},null,{timeout:60000});
  result.initial_full_ms=Date.now()-entered;
  result.initial=await page.evaluate(()=>{
    const e=window.__game.coarse.entries.get('t0_0_0');
    return {hash:e.visualHash,stale:e.stale,collision_pending:e.collisionPending,cells:e.collision.size};
  });
  console.log(JSON.stringify({label,stage:'initial-full',...result.initial,ms:result.initial_full_ms}));
  await page.evaluate(()=>{
    const game=window.__game, coarse=game.coarse, camera=window.__render.camera;
    const scene=game.tiles.models.values().next().value.parent;
    window.__revisitPose={x:-875,z:750};
    const update=coarse.update.bind(coarse), stream=game.streamer.update.bind(game.streamer);
    coarse.setRouteHint(null);
    coarse.update=()=>{const p=window.__revisitPose;update(p.x,p.z,performance.now());};
    game.streamer.update=()=>{const p=window.__revisitPose;stream(p.x,p.z,false,0,0);};
    const before=scene.onBeforeRender;
    scene.onBeforeRender=function(...args){before.apply(this,args);if(args[2]!==camera)return;
      const p=window.__revisitPose;camera.position.set(p.x-6,30,p.z+3);camera.lookAt(p.x+25,5,p.z);camera.updateMatrixWorld();};
  });
  await page.waitForFunction(()=>!window.__game.coarse.entries.has('t0_0_0'),null,{timeout:15000});
  const returned=Date.now();
  await page.evaluate(()=>{window.__revisitPose={x:168,z:246};});
  await page.waitForFunction(()=>{const e=window.__game.coarse.entries.get('t0_0_0');return e?.model&&e.pass==='full';},null,{timeout:60000});
  result.revisit_full_ms=Date.now()-returned;
  result.revisit=await page.evaluate(()=>{
    const game=window.__game,e=game.coarse.entries.get('t0_0_0');
    return {hash:e.visualHash,stale:e.stale,collision_pending:e.collisionPending,cells:e.collision.size,
      supported:game.streamer.supports(168,246)};
  });
  await page.screenshot({path:path.join(out,'revisit.png')});
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  if(result.errors.length)throw new Error('Page errors during retained revisit');
} catch(error) {
  result.failure=String(error);
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify(result,null,2));
  throw error;
} finally {clearTimeout(hardStop);await context.close();await room.close();}
