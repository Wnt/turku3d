// Own sandbox server, without production producer notifications.
import {createServer} from '../../server/http.js';
import {ScoreStore} from '../../server/store.js';
import {port,workRoot} from '../dev/environment.mjs';
import path from 'node:path';
let offset=0;
const app=createServer({now:()=>Date.now()+offset,store:new ScoreStore(path.join(workRoot,'video/mp-scores'))});
const original=app.server.listeners('request')[0];app.server.removeListener('request',original);
app.server.on('request',(req,res)=>{const u=new URL(req.url,'http://localhost');if(u.pathname==='/capture/advance'){offset+=Number(u.searchParams.get('ms')??0);app.room.tick();res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({offset}));}else original(req,res);});
app.server.listen(port(4),'127.0.0.1',()=>console.log(`Capture multiplayer ${port(4)}`));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
