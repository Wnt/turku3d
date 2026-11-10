// Browser QA only through a leased, owned headless Chrome. No game navigation.
import {readFileSync,writeFileSync,statSync} from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const work=process.env.SOUND_WORK||`${process.env.HOME}/turku3d/loops/sounds-astra`;
const {chromium}=await import(pathToFileURL(`${work}/work/node/node_modules/playwright/index.mjs`));
const board=`${work}/board`;
const types={'.html':'text/html; charset=utf-8','.json':'application/json','.ogg':'audio/ogg','.png':'image/png','.txt':'text/plain; charset=utf-8'};
const server=http.createServer((req,res)=>{
 try{const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);const file=path.resolve(board,'.'+(pathname==='/'?'/index.html':pathname));if(!file.startsWith(board+'/'))throw Error('path');const data=readFileSync(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'text/plain','Content-Length':data.length});res.end(data);}catch{res.writeHead(404);res.end('not found');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const url=process.env.SOUND_BOARD_URL||`http://127.0.0.1:${server.address().port}/`;
const browser=await chromium.connectOverCDP(process.env.BROWSER_CDP);
const browserContext=browser.contexts()[0];
const page=await browserContext.newPage();
await page.setViewportSize({width:1440,height:1000});
const errors=[],external=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',request=>{const u=request.url();if(!u.startsWith(new URL(url).origin)&&!u.startsWith('blob:')&&!u.startsWith('data:'))external.push(u);});
await page.goto(url,{waitUntil:'networkidle'});
await page.screenshot({path:`${work}/evidence/board-desktop.png`});
assert.equal(await page.locator('html').getAttribute('lang'),'fi');
for(const [group,id] of [['gulls','current-gulls'],['tyres','current-screech'],['music','current-music-aura-neon']])assert.equal(await page.locator(`#${group} .card`).first().getAttribute('data-id'),id);
await page.locator('[data-pick="g02-herring-series"]').check();
await page.locator('[data-pick="m01-river-afterhours"]').check();
await page.reload({waitUntil:'networkidle'});
assert.equal(await page.locator('[data-pick="g02-herring-series"]').isChecked(),true);
assert.equal(await page.locator('[data-pick="m01-river-afterhours"]').isChecked(),true);
await browserContext.grantPermissions(['clipboard-read','clipboard-write'],{origin:new URL(url).origin});
await page.locator('#copy').click();
const copied=await page.evaluate(()=>navigator.clipboard.readText());
assert(copied.includes('g02-herring-series')&&copied.includes('m01-river-afterhours'));
await page.locator('[data-play="g02-herring-series"]').click();
await page.waitForFunction(()=>document.querySelector('[data-id="g02-herring-series"]').classList.contains('playing'));
await page.locator('[data-seam="t04-drift-heavy"]').click();
await page.waitForFunction(()=>document.querySelector('[data-id="t04-drift-heavy"]').classList.contains('playing'));
await page.waitForTimeout(2200);
const loopPosition=Number(await page.locator('[data-seek="t04-drift-heavy"]').inputValue());assert(loopPosition<2,`loop position ${loopPosition}`);
await page.locator('#stop').click();
await page.locator('#liveStart').click();
await page.waitForFunction(()=>document.querySelector('#liveStart').textContent==='Pysäytä rengasmalli');
await page.locator('[data-preset="gravel"]').click();
assert.equal(await page.locator('#surface').inputValue(),'2');
await page.locator('#tyreLab').scrollIntoViewIfNeeded();
await page.screenshot({path:`${work}/evidence/board-tyres.png`});
await page.locator('#stop').click();
const decoded=await page.evaluate(async()=>{
 audioContext();const report=[];
 for(const item of CATALOGUE){const buffer=await loadBuffer(item);let peak=0;for(let c=0;c<buffer.numberOfChannels;c++){const data=buffer.getChannelData(c);for(let i=0;i<data.length;i+=37)peak=Math.max(peak,Math.abs(data[i]));}report.push({id:item.id,duration:buffer.duration,peak,channels:buffer.numberOfChannels});}
 return report;
});
for(const row of decoded){assert(row.peak>.0001,`${row.id} silent`);assert(row.peak<1,`${row.id} clips`);}
await page.locator('[data-pick="g02-herring-series"]').uncheck();await page.locator('[data-pick="m01-river-afterhours"]').uncheck();
await page.setViewportSize({width:390,height:844});await page.goto(url);await page.screenshot({path:`${work}/evidence/board-mobile.png`});
assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile overflow');
assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
const result={url,decoded,clipboard:copied,loopPosition,pageErrors:errors,externalRequests:external,checks:['current sound first','selection persistence','clipboard export','playback and switching','loop boundary playback','live AudioWorklet and presets','all files decode with signal and headroom','mobile width','no external requests']};
writeFileSync(`${work}/evidence/browser-check.json`,JSON.stringify(result,null,2));console.log(JSON.stringify({status:'PASS',files:decoded.length,checks:result.checks},null,2));
await page.close();await browser.close();await new Promise(resolve=>server.close(resolve));
