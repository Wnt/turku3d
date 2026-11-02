// Execute the coordinator's actual check with its supported owned-profile/output options.
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import {pathToFileURL} from 'node:url';
import {lease,owner} from './comparison-browser.mjs';
const root=path.join(os.homedir(),'turku3d');
process.env.TURKU3D_VERIFY_PROFILE=path.join(root,`loops/${owner}/verify-browser`);
process.env.TURKU3D_VERIFY_OUT=path.join(root,`loops/${owner}/verify-5220`);
lease('acquire');try{await import(pathToFileURL(path.join(root,'loops/coordinator/verify-5220.mjs')));}finally{lease('release');}

const result=JSON.parse(fs.readFileSync(path.join(process.env.TURKU3D_VERIFY_OUT,'result.json')));if(!result.ok||result.errors?.length)throw Error(JSON.stringify(result));
