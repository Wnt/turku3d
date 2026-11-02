import {chromium} from '../../game/node_modules/playwright-core/index.mjs';
import {chromeArgs,chromeExecutable,chromeIgnoreDefaultArgs} from '../dev/chrome-args.mjs';
import {spawnSync} from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
const guard=path.join(os.homedir(),'turku3d/tools/chrome-guard/guard.py');
export function lease(action){const r=spawnSync('python3',[guard,action,'video'],{encoding:'utf8'}); if(action==='acquire'&&r.status)throw Error(r.stdout+r.stderr);return r.status;}
export async function launch(width,height){
 lease('acquire');
 try{return await chromium.launchPersistentContext(path.join(os.homedir(),'turku3d/loops/video/browser'),{
  executablePath:chromeExecutable(),ignoreDefaultArgs:chromeIgnoreDefaultArgs,headless:true,
  viewport:{width,height},deviceScaleFactor:1,args:chromeArgs(['--enable-unsafe-webgpu','--use-angle=metal','--ignore-gpu-blocklist','--remote-debugging-port=5963','--disable-background-timer-throttling'])
 });}catch(e){lease('release');throw e;}
}
