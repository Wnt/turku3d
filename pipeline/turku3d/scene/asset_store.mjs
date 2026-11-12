// Immutable authored inputs live beside the generated prepared asset store.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
const expand=value=>value.startsWith('~/')?path.join(os.homedir(),value.slice(2)):value;
export function resolvePreparedAsset(input,{assetRoot}={}){
 const requested=path.resolve(expand(input)),name=path.basename(requested),hashed=/^[0-9a-f]{64}\.[a-zA-Z0-9]+$/.test(name);
 const root=assetRoot??path.join(process.env.TURKU3D_DATA??path.join(os.homedir(),'turku3d/data'),'prepared-assets');
 const candidate=fs.existsSync(requested)?requested:path.join(expand(root),'shared',name);
 if(!fs.existsSync(requested)&&!hashed)throw Error('Missing authored source asset: '+requested);
 if(!fs.existsSync(candidate))throw Error('Missing content-addressed asset: '+candidate);
 if(hashed&&crypto.createHash('sha256').update(fs.readFileSync(candidate)).digest('hex')!==path.parse(name).name)throw Error('Prepared asset hash mismatch: '+name);
 return candidate;
}
