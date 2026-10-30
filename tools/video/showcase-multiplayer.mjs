// Own sandbox server, without production producer notifications.
import {createServer} from '../../server/http.js';
import {ScoreStore} from '../../server/store.js';
import {port,workRoot} from '../dev/environment.mjs';
import path from 'node:path';
const app=createServer({store:new ScoreStore(path.join(workRoot,'video/mp-scores'))});
app.server.listen(port(4),'127.0.0.1',()=>console.log(`Capture multiplayer ${port(4)}`));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
