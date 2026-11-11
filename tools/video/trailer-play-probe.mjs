// Probe: boot the play mode in the leased browser and report what the session exposes.
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
const b=await chromium.connectOverCDP(process.env.BROWSER_CDP),ctx=b.contexts()[0];
await ctx.addInitScript(()=>Object.defineProperty(navigator,'getGamepads',{value:()=>[]}));
const page=await ctx.newPage();
page.on('pageerror',e=>console.log('PAGE ERROR',e.message.slice(0,300)));
const url=process.argv[2]??'http://127.0.0.1:5970/?spawn=23459652,6704624,135&hud=0&quality=low&time=2026-09-08T15:00:00%2B03:00&traffic=0&walkers=0';
await page.goto(url,{waitUntil:'domcontentloaded',timeout:120000});
await page.waitForFunction(()=>window.__game?.ready,null,{timeout:300000});
console.log('ready',new Date().toISOString().slice(11,19));
console.log(await page.evaluate(()=>({options:[...document.querySelectorAll('[data-option]')].map(e=>e.dataset.option+':'+e.textContent.trim().slice(0,30)),
 game:Object.keys(window.__game).sort().join(' '),state:JSON.stringify(window.__game.state()).slice(0,900)})));
await b.close();
