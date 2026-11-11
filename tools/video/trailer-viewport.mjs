// Debug: resize the viewport of the session's page of one quality tier (node trailer-viewport.mjs PORT ultra 480 270).
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
const [port,quality,w,h]=process.argv.slice(2);
const b=await chromium.connectOverCDP(`http://127.0.0.1:${port}`),ctx=b.contexts()[0];
for(const p of ctx.pages()){
 if(await p.evaluate(()=>window.__game?.params?.quality).catch(()=>null)!==quality)continue;
 const s=await ctx.newCDPSession(p);await s.send('Emulation.setDeviceMetricsOverride',{width:Number(w),height:Number(h),deviceScaleFactor:1,mobile:false});
 console.log('resized',await p.evaluate(()=>[innerWidth,innerHeight,window.__game.scenery.progress.loaded,window.__game.scenery.progress.total,window.__perf.frameMs?.p50]));
}
await b.close();
