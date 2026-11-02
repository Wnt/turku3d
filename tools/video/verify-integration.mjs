// Execute the coordinator's actual check, substituting only our guarded browser profile.
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import {pathToFileURL} from 'node:url';
import {lease} from './comparison-browser.mjs';
const root=path.join(os.homedir(),'turku3d');
const {chromium}=await import(pathToFileURL(path.join(root,'wt/loop-integration/repo/game/node_modules/@playwright/test/index.mjs')));
const original=chromium.launchPersistentContext.bind(chromium);
chromium.launchPersistentContext=(_profile,options)=>original(path.join(root,'loops/video/verify-browser'),options);
lease('acquire');try{await import(pathToFileURL(path.join(root,'loops/coordinator/verify-5220.mjs')));}finally{lease('release');}

const result=JSON.parse(fs.readFileSync(path.join(root,'loops/coordinator/verify/result.json')));if(!result.ok)throw Error(JSON.stringify(result));
