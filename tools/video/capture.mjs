// Deterministic 1080p WebGPU cinematography through the game's existing QA hooks.
import { chromium } from '../../game/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { isolateBrowser, port, workRoot } from '../dev/environment.mjs';

isolateBrowser();
const root = path.join(workRoot, 'video');
const mode = process.argv[2] ?? 'probe';
const selected = process.argv[3];
const shots = [
  { id: 'office', seconds: 14, from: [165, 7.2, 271, 325, 0], to: [178, 7.2, 262, 322, 0] },
  { id: 'riverside', seconds: 14, from: [125, 12, 300, 65, -12], to: [155, 13, 282, 65, -12] },
  { id: 'detail', seconds: 13, from: [147, 6.8, 272, 310, 1], to: [163, 6.8, 261, 315, 1] },
  { id: 'overhead', seconds: 16, from: [168, 240, 246, 0, -89.9], to: [168, 225, 246, 0, -89.9] },
  { id: 'aerial', seconds: 16, from: [100, 100, 355, 40, -35], to: [170, 95, 365, 30, -35] },
  { id: 'draft', seconds: 16, from: [625, 210, 160, 0, -75], to: [650, 200, 140, 0, -75], draft: true },
  { id: 'bridge', seconds: 16, from: [610, 95, 190, 150, -42], to: [660, 95, 178, 145, -42], draft: true },
];
fs.mkdirSync(path.join(root, 'shots'), { recursive: true });
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, 'video'), {
  channel: 'chrome', headless: true, viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1,
  args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling'],
});
const page = await context.newPage();
// This is a camera-only render. Prevent any connection to shared/public gameplay services.
await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
const errors = [];
page.on('pageerror', e => errors.push(e.message));
try {
  for (const shot of shots.filter(s => !selected || s.id === selected)) {
    if (shot.draft && !fs.existsSync(path.join(workRoot, 'coarse/tileset.json'))) continue;
    const pose = p => ({ E: 23459500+p[0], N: 6704500-p[2], H:p[1], yaw:p[3], pitch:p[4], fov:55 });
    const initial = pose(shot.from);
    const url = new URL(`http://127.0.0.1:${port(1)}/`);
    url.searchParams.set('backend','webgpu'); url.searchParams.set('quality','high');
    url.searchParams.set('time','2026-08-15T12:00:00+03:00');
    url.searchParams.set('tiles',shot.draft ? '/draft/tileset.json' : '/tiles/tileset.json');
    url.searchParams.set('cam',[initial.E,initial.N,initial.H,initial.yaw,initial.pitch,initial.fov].join(','));
    await page.goto(url.href);
    await page.waitForFunction(() => window.__qaReady || window.__qaError, null, { timeout:180000 });
    const info = await page.evaluate(() => ({ error:window.__qaError,info:window.__qaInfo,qa:window.__game.qa, matrix:window.__game.tiles.tiles.group.matrixWorld.toArray(), children:window.__game.tiles.tiles.group.children.map(x=>({position:x.position.toArray(),rotation:x.rotation.toArray(),matrix:x.matrix.toArray()})) }));
    if(info.error) throw new Error(info.error);
    if(info.info.backend !== 'WebGPU') throw new Error('WebGPU required');
    await page.screenshot({path:path.join(root,'shots',`${shot.id}-probe.png`)});
    fs.writeFileSync(path.join(root,'shots',`${shot.id}.json`),JSON.stringify({shot,info,errors},null,2));
    console.log(`${shot.id}: loaded ${info.qa.models} models`);
    if(mode === 'probe') continue;
    // Preload both endpoints, then return to the first before locking the render loop.
    for(const p of [shot.to,shot.from]) {
      await page.evaluate(p=>window.__qaMove(p),pose(p));
      await page.waitForFunction(()=>window.__qaReady || window.__qaError,null,{timeout:180000});
    }
    await page.evaluate(()=>{
      window.__videoTime=performance.now();
      window.__videoPermit=false;
      window.__videoDone=false;
      performance.now=()=>window.__videoTime;
      window.__render.beforeFrame=()=>window.__videoPermit;
      window.__render.afterFrame=()=>{window.__videoPermit=false;window.__videoDone=true;};
    });
    const output=path.join(root,'shots',`${shot.id}.mp4`);
    const encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-vcodec','mjpeg','-r','30','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-profile:v','high','-threads','3',output],{stdio:['pipe','inherit','inherit']});
    for(let frame=0;frame<shot.seconds*30;frame++) {
      const u=frame/(shot.seconds*30-1);
      const p=pose(shot.from.map((v,i)=>v+(shot.to[i]-v)*u));
      await page.evaluate(p=>{
        window.__qaMove(p); window.__videoTime+=1000/30; window.__videoDone=false;window.__videoPermit=true;
      },p);
      await page.waitForFunction(()=>window.__videoDone,null,{polling:'raf'});
      const bytes=await page.screenshot({type:'jpeg',quality:95});
      if(!encoder.stdin.write(bytes)) await once(encoder.stdin,'drain');
    }
    encoder.stdin.end();
    const [code]=await once(encoder,'exit');
    if(code) throw new Error(`ffmpeg ${code}`);
    console.log(`${shot.id}: ${shot.seconds*30} deterministic frames saved`);
  }
} finally { await context.close(); }
