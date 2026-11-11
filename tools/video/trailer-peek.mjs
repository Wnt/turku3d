// Debug: evaluate an expression in the capture page of a running session (node trailer-peek.mjs PORT 'expr').
import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
const b=await chromium.connectOverCDP(`http://127.0.0.1:${process.argv[2]}`);
for(const p of b.contexts()[0].pages()){if(!p.url().startsWith('http'))continue;
 try{console.log(p.url().slice(0,80),JSON.stringify(await p.evaluate(process.argv[3]),null,1));}catch(e){console.log('ERR',e.message.slice(0,500));}}
await b.close();
