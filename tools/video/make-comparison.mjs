#!/usr/bin/env node
// One command, no production writes. All products and browser state live outside git.
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';
import {spawn,spawnSync,execFileSync} from 'node:child_process';import {once} from 'node:events';
import {fileURLToPath} from 'node:url';import {allTiles} from './paths.mjs';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),root=path.join(os.homedir(),'turku3d');
const args=process.argv.slice(2);
if(args.includes('--help')){console.log('Usage: node tools/video/make-comparison.mjs [--left URL] [--right URL] [--shots FILE] [--out DIR] [--preview | --skip-preview]');process.exit(0);}
const opt=(k,d)=>args.includes(k)?args[args.indexOf(k)+1]:d;
const out=path.resolve(opt('--out',path.join(root,'video',new Date().toISOString().slice(0,10))));fs.mkdirSync(out,{recursive:true});
const shots=path.resolve(opt('--shots',path.join(repo,'tools/video/comparison-shots.json'))),config=JSON.parse(fs.readFileSync(shots));
const integration=path.join(root,'wt/loop-integration/repo'),state=path.join(root,'loops/video'),owned=[];
const git=(cwd,...a)=>execFileSync('git',a,{cwd,encoding:'utf8'}).trim();
const ref=git(integration,'rev-parse','HEAD'),alias=`video-${ref.slice(0,8)}`,production=git(repo,'rev-parse','main');
const serviceConfig=path.join(root,'loops/integrator/tile-service.json'),service=JSON.parse(fs.readFileSync(serviceConfig));
let left=opt('--left','http://127.0.0.1:5960/'),right=opt('--right','http://localhost:5220/');
if(new URL(left).hostname==='wnt.github.io'&&/^\/turku-game\/?$/.test(new URL(left).pathname))left='http://127.0.0.1:5960/';
const log=s=>console.log(new Date().toISOString(),s);
async function run(command,argv,cwd=repo){const p=spawn(command,argv,{cwd,stdio:'inherit'});const [code]=await once(p,'close');if(code!==0)throw Error(`${command} exited ${code}`);}
async function up(url){try{return(await fetch(url,{signal:AbortSignal.timeout(1000)})).ok;}catch{return false;}}
async function start(command,argv,cwd,env,url){if(await up(url))return;const p=spawn(command,argv,{cwd,env:{...process.env,...env},stdio:['ignore','inherit','inherit']});owned.push(p);for(let i=0;i<60;i++){if(await up(url))return;await new Promise(r=>setTimeout(r,500));}throw Error(`Server did not start: ${url}`);}
async function prepareProduction(){
 const marker=path.join(state,'production-server.json');
 if(await up(left)){
  if(!fs.existsSync(marker)||JSON.parse(fs.readFileSync(marker)).revision!==production)throw Error('Port 5960 is occupied without a matching video production receipt');
  return;
 }
 const snapshot=path.join(state,'production',production);if(!fs.existsSync(path.join(snapshot,'game/package.json'))){
  fs.mkdirSync(snapshot,{recursive:true});const ar=spawn('git',['archive',production],{cwd:repo,stdio:['ignore','pipe','inherit']});const unpack=spawn('tar',['-x','-C',snapshot],{stdio:['pipe','inherit','inherit']});ar.stdout.pipe(unpack.stdin);if((await once(unpack,'close'))[0])throw Error('Production snapshot extraction failed');
 }
 const modules=path.join(snapshot,'game/node_modules');if(!fs.existsSync(modules))fs.symlinkSync(path.join(integration,'game/node_modules'),modules);
 await start('node',[path.join(repo,'tools/video/published-proxy.mjs')],repo,{},'http://127.0.0.1:5965/coarse/status');
 await start(path.join(modules,'.bin/vite'),['--host','127.0.0.1'],path.join(snapshot,'game'),{TURKU3D_PORT_BASE:'5960',TURKU3D_WORK:path.join(state,'production-output')},left);
 fs.writeFileSync(marker,JSON.stringify({revision:production,snapshot}));
}
async function requestTiles(tiles){for(let i=0;i<tiles.length;i+=4)await Promise.all(tiles.slice(i,i+4).map(async tile=>{const r=await fetch(`${service.service}/v/${alias}/coarse/${tile}/manifest.json`,{signal:AbortSignal.timeout(30000)});return r.json();}));}
async function waitFresh(tiles){
 const start=Date.now();let last='';
 while(true){let missing=[];for(let i=0;i<tiles.length;i+=4)await Promise.all(tiles.slice(i,i+4).map(async tile=>{const r=await fetch(`${service.service}/v/${alias}/coarse/${tile}/manifest.json`,{signal:AbortSignal.timeout(30000)}),m=await r.json();if(m.stale||m.pass_kind!=='full'||m.revision!==ref)missing.push(tile);}));
  const message=`Fresh tiles ${tiles.length-missing.length}/${tiles.length}`;if(message!==last){log(message);last=message;}
  if(!missing.length)return;
  if(Date.now()-start>4*3600000)throw Error(`Tiles still unavailable: ${missing.join(' ')}`);
  await new Promise(r=>setTimeout(r,30000));
 }
}
try{
 fs.writeFileSync(path.join(out,'builds.json'),JSON.stringify({pipeline:git(repo,'rev-parse','HEAD'),production,integration:ref,alias,left,right,created:new Date().toISOString()},null,2));
 fs.copyFileSync(shots,path.join(out,'shot-list.json'));
 log(`Registering ${alias}`);await run('scripts/dev/shared-tiles',['register','--alias',alias,'--ref',ref],integration);
 let tiles=allTiles(config);await requestTiles(tiles);fs.writeFileSync(path.join(out,'tiles.json'),JSON.stringify(tiles,null,2));
 if(left.includes(':5960'))await prepareProduction();
 const tileProxy=path.join(state,'tile-service.json');fs.writeFileSync(tileProxy,JSON.stringify({...service,version:alias}));
 await start('node',[path.join(repo,'scripts/dev/shared-tile-proxy.mjs'),'5966',tileProxy],repo,{},'http://127.0.0.1:5966/coarse/status');
 const capture=[path.join(repo,'tools/video/comparison-capture.mjs'),'--left',left,'--right',right,'--shots',shots,'--out',out,'--revision',ref];
 if(!args.includes('--skip-preview'))await run('node',[...capture,'--preview','--right-tiles','http://127.0.0.1:5966']);
 // The real resident window can exceed the minimum safety ring. Rebuild every observed detailed tile too.
 const preview=path.join(out,'preview');if(fs.existsSync(preview))for(const name of fs.readdirSync(preview).filter(n=>n.endsWith('-right.json'))){const r=JSON.parse(fs.readFileSync(path.join(preview,name)));tiles.push(...r.resident.filter(r=>!r.preview).map(r=>r.id));}
 tiles=[...new Set(tiles)].sort();fs.writeFileSync(path.join(out,'tiles.json'),JSON.stringify(tiles,null,2));await requestTiles(tiles);
 if(args.includes('--preview')){await run('bash',['pipeline/env/run.sh','python',path.join(repo,'tools/video/comparison-compose.py'),'--out',out,'--shots',shots,'--preview']);}
 else{
  await waitFresh(tiles);
  if(git(integration,'rev-parse','HEAD')!==ref)throw Error('Integration source changed; re-run to pin a coherent source/tile revision');
  const latest=JSON.parse(fs.readFileSync(serviceConfig));latest.version=alias;fs.writeFileSync(serviceConfig,JSON.stringify(latest,null,2)+'\n');
  await run('node',[path.join(repo,'tools/video/verify-integration.mjs')]);
  await run('node',capture);
  await run('bash',['pipeline/env/run.sh','python',path.join(repo,'tools/video/comparison-compose.py'),'--out',out,'--shots',shots]);
  await run('bash',['pipeline/env/run.sh','python',path.join(repo,'tools/video/comparison-review.py'),'--out',out]);
 }
}finally{for(const p of owned.reverse())p.kill('SIGTERM');}
