// Capture the live vehicle studio over its owned CDP connection. No browser launch or game reload.
import fs from 'node:fs';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { chromium } from '../../game/node_modules/playwright-core/index.mjs';
const option=(name,fallback)=>{const i=process.argv.indexOf(name);return i<0?fallback:process.argv[i+1];};
const mode=option('--mode','orbit'),out=option('--out',null),fps=Number(option('--fps','30'));
const frames=Number(option('--frames',mode==='drive'?'360':'180'));
if(!out||!['orbit','drive'].includes(mode))throw new Error('--out and --mode orbit|drive required');
const browser=await chromium.connectOverCDP(`http://localhost:${option('--cdp','5927')}`);
const page=browser.contexts()[0].pages().find(p=>p.url().includes('prerunner-lab'));
if(!page)throw new Error('Open the vehicle studio in the owned browser first');
await page.waitForFunction(()=>window.__prerunner,null,{timeout:120000});
await page.bringToFront();const cdp=await page.context().newCDPSession(page);
await cdp.send('Emulation.setFocusEmulationEnabled',{enabled:true});
await page.evaluate(async mode=>{const a=window.__prerunner;a.renderer.setAnimationLoop(null);a.orbit(false);a.articulate(false);if(mode==='drive')await a.drive();},mode);
const encoder=spawn('nice',['-n','10','ffmpeg','-v','error','-y','-f','image2pipe','-framerate',`${fps}`,'-c:v','mjpeg','-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',out],{stdio:['pipe','ignore','inherit']});
try{
 for(let i=0;i<frames;i++){
  await page.evaluate(({mode,i,frames,fps})=>{const a=window.__prerunner;if(mode==='orbit')a.pose(-.8+i/frames*Math.PI*2,.28+.04*Math.sin(i/frames*Math.PI*2),8.6);else a.stepDrive(1/fps);a.controls.update();a.renderer.render(a.scene,a.camera);},{mode,i,frames,fps});
  const shot=await cdp.send('Page.captureScreenshot',{format:'jpeg',quality:86,fromSurface:true,clip:{x:0,y:0,width:1600,height:1000,scale:.8}});
  if(!encoder.stdin.write(Buffer.from(shot.data,'base64')))await once(encoder.stdin,'drain');
  if(i%60===0)console.log(JSON.stringify({frame:i,total:frames,mode}));
 }
 const state=await page.evaluate(()=>window.__prerunner.driveStats());
 encoder.stdin.end();const [code]=await once(encoder,'exit');if(code)throw new Error(`ffmpeg exit ${code}`);
 fs.writeFileSync(out+'.json',JSON.stringify({mode,fps,frames,release:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),drive:state,note:'Fixed-step frame export; use CameraBench for actual game FPS.'},null,2));
 console.log(JSON.stringify({saved:out,mode,frames}));
}finally{encoder.stdin.end();await browser.close();}
