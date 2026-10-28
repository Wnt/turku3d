import { chromeArgs } from '../dev/chrome-args.mjs';
// Fixed-step WebGPU camera splines and physics-driven cars; all traffic stays in this sandbox.
import { chromium } from '../../game/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { isolateBrowser, port, workRoot } from '../dev/environment.mjs';

isolateBrowser();
const root=path.join(workRoot,'video');
const mode=process.argv[2]??'probe';
const selection=process.argv[3]?.split(',');
const shots=JSON.parse(fs.readFileSync('tools/video/shots.json'));
const timelinePath=path.join(root,'timeline.json');
if(fs.existsSync(timelinePath))for(const scene of JSON.parse(fs.readFileSync(timelinePath))) {
  for(const id of scene.shots)shots.find(s=>s.id===id).seconds=scene.duration/scene.shots.length;
}
fs.mkdirSync(path.join(root,'shots'),{recursive:true});
const context=await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR,'video-v2'),{
 channel:'chrome',headless:true,viewport:{width:1920,height:1080},deviceScaleFactor:1,
 args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--disable-background-timer-throttling']),
});
const page=await context.newPage();
await page.route('**/*',route=>{
 const url=new URL(route.request().url());
 return url.hostname==='127.0.0.1' && url.port===String(port(1)) ? route.continue() : route.abort();
});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try {
 for(const shot of shots.filter(s=>!selection||selection.includes(s.id))) {
  const p=shot.points[0],t=shot.targets[0],d=t.map((v,i)=>v-p[i]);
  const cam=[23459500+p[0],6704500-p[2],p[1],Math.atan2(d[0],-d[2])*180/Math.PI,Math.atan2(d[1],Math.hypot(d[0],d[2]))*180/Math.PI,55];
  const url=new URL(`http://127.0.0.1:${port(1)}/`);
  for(const [key,value] of Object.entries({backend:'webgpu',quality:'high',time:shot.time??'2026-08-15T15:00:00+03:00',tiles:shot.tileset??'/tiles/capture.json',cam:cam.join(',')}))url.searchParams.set(key,value);
  await page.goto(url.href);
  await page.waitForFunction(()=>window.__qaReady||window.__qaError,null,{timeout:180000});
  const info=await page.evaluate(()=>({error:window.__qaError,info:window.__qaInfo,qa:window.__game.qa}));
  if(info.error)throw new Error(`${shot.id}: ${info.error}`);
  if(info.info.backend!=='WebGPU')throw new Error('WebGPU required');
  await page.evaluate(async shot=>{
   window.__cinema=await import('/assets/video.js');
   window.__pose=window.__cinema.curves(shot.points,shot.targets);
   window.__drive=shot.drive ? await window.__cinema.car(shot.drive,shot.speed) : null;
  },shot);
  for(const u of [0,.5,1,0]) {
   await page.evaluate(u=>window.__qaMove(window.__pose(u)),u);
   await page.waitForFunction(()=>window.__qaReady||window.__qaError,null,{timeout:180000});
  }
  if(mode==='music') {
   const audio=await page.evaluate(()=>window.__cinema.music(300));
   fs.writeFileSync(path.join(root,'audio/music.wav'),Buffer.from(audio,'base64'));
   console.log('Music: original Harbour Glass rendered by game MusicSystem');break;
  }
  await page.evaluate(async ()=>{
   if(window.__drive)for(let i=0;i<90;i++)await window.__drive();
   window.__videoTime=performance.now();window.__videoPermit=false;window.__videoDone=false;
   performance.now=()=>window.__videoTime;
   window.__render.beforeFrame=()=>window.__videoPermit;
   window.__render.afterFrame=()=>{window.__videoPermit=false;window.__videoDone=true;};
  });
  const frames=mode==='probe'?3:Math.ceil(shot.seconds*30);
  let encoder;
  if(mode==='capture')encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-vcodec','mjpeg','-r','30','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-profile:v','high','-threads','3',path.join(root,'shots',`${shot.id}.mp4`)],{stdio:['pipe','inherit','inherit']});
  const motion=[];
  for(let f=0;f<frames;f++) {
   const range=shot.range??[0,1];
   const u=range[0]+f/(frames-1)*(range[1]-range[0]);
   const state=await page.evaluate(async ({u,drive,probe})=>{
    let pose=window.__pose(u),state=null;
    if(drive){
     if(probe)for(let i=0;i<60;i++)state=await window.__drive();
     else state=await window.__drive();
     const s=state,heading=Math.atan2(s.linvel.z,s.linvel.x);
     const yaw=heading-0.45;
     const x=s.pos.x-9*Math.cos(yaw),z=s.pos.z-9*Math.sin(yaw);
     pose={E:23459500+x,N:6704500-z,H:s.pos.y+4.5,yaw:Math.atan2(s.pos.x-x,-(s.pos.z-z))*180/Math.PI,pitch:-18,fov:55};
    }
    window.__qaMove(pose);window.__videoTime+=1000/30;window.__videoDone=false;window.__videoPermit=true;
    return {pose,vehicle:state};
   },{u,drive:!!shot.drive,probe:mode==='probe'});
   await page.waitForFunction(()=>window.__videoDone,null,{polling:'raf'});
   if(shot.marker)await page.evaluate(marker=>{
    // Project the surveyed office point through the actual game camera.
    const camera=window.__render.camera;
    const mul=(m,v)=>[0,1,2,3].map(r=>v.reduce((s,x,c)=>s+m[c*4+r]*x,0));
    const q=mul(camera.projectionMatrix.elements,mul(camera.matrixWorldInverse.elements,[...marker.point,1]));
    let el=document.getElementById('video-survey-marker');
    if(!el){el=document.createElement('div');el.id='video-survey-marker';document.body.append(el);}
    el.style.cssText=`position:fixed;left:${(q[0]/q[3]+1)*960-24}px;top:${(1-q[1]/q[3])*540-24}px;width:48px;height:48px;border:4px solid #75e7cf;border-radius:50%;z-index:9999;pointer-events:none`;
    el.textContent='';const label=document.createElement('span');label.textContent=marker.label;
    label.style.cssText='position:absolute;left:58px;top:3px;padding:7px 12px;background:#061019e8;color:#75e7cf;font:bold 23px Arial;white-space:nowrap';el.append(label);
   },shot.marker);
   if(f%30===0||mode==='probe')motion.push({frame:f,...state});
   if(mode==='probe')await page.screenshot({path:path.join(root,'shots',`${shot.id}-probe-${f}.png`)});
   else {
    const bytes=await page.screenshot({type:'jpeg',quality:95});
    if(!encoder.stdin.write(bytes))await once(encoder.stdin,'drain');
   }
  }
  if(encoder){encoder.stdin.end();const [code]=await once(encoder,'exit');if(code)throw new Error(`ffmpeg ${code}`);}
  fs.writeFileSync(path.join(root,'shots',`${shot.id}.json`),JSON.stringify({shot,info,motion,errors},null,2));
  console.log(`${shot.id}: ${frames} frames; ${info.qa.models} models; ${errors.length} page errors`);
 }
} finally {await context.close();}
