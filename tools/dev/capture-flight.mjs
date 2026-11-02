// Native play-mode flight evidence, invoked only by capture-loop.mjs.
import fs from 'node:fs';
import path from 'node:path';
export async function captureFlight({send,evaluate,delay,out,timeout}) {
 fs.mkdirSync(out,{recursive:true});
 await send('Page.setWebLifecycleState',{state:'active'});
 const start=Date.now();
 await evaluate('window.__render.beforeFrame=undefined');
 while(!await evaluate(`window.__game?.ready && window.__game.loadProgress?.().phase==='ready' && document.querySelector('#start-screen [data-option="new"]')?.getAttribute('aria-disabled')==='false'`)) {
  if(Date.now()-start>timeout)throw Error('Play menu readiness timed out');await delay(500);
 }
 await evaluate(`document.querySelector('#start-screen [data-option="new"]').click()`);
 while(await evaluate('window.__game.state().startScreen || window.__game.state().loading')) {
  if(Date.now()-start>timeout)throw Error('Play startup timed out');await delay(500);
 }
 async function key(code,key,type){await send('Input.dispatchKeyEvent',{type,code,key,windowsVirtualKeyCode:code==='Space'?32:code==='ShiftLeft'?16:87});}
 if(!await evaluate('window.__game.state().fly'))for(let i=0;i<2;i++){
  await key('Space',' ','keyDown');await delay(80);await key('Space',' ','keyUp');await delay(80);
 }
 await evaluate(`(()=>{const g=window.__game;if(!g.state().fly)throw Error('Native double-jump did not enable flight');g.physics.setCharacterEnabled(true,{x:300,y:120,z:150});g.orbit.yaw=0;g.orbit.pitch=-15*Math.PI/180;window.__render.setHour(7.5);})()`);
 await delay(500);
 const flight=[];
 const state=`(()=>{const g=window.__game,c=window.__render.camera,p=c.position;
  const under=[...g.scenery.entries].filter(([id,e])=>{const [l,i,j]=id.slice(1).split('_').map(Number),size=250*2**l;return e.model?.visible&&p.x>=i*size&&p.x<(i+1)*size&&p.z>=j*size&&p.z<(j+1)*size;}).map(([id])=>({id,regional:!!g.scenery.sources.get(id)?.regional}));
  const s=g.state();return{fly:s.fly,camera:p.toArray(),groundOwners:under,coverage:g.coarse.viewStatus(p.x,p.z),state:s};})()`;
 await key('ShiftLeft','Shift','keyDown');await key('KeyW','w','keyDown');
 for(let i=0;i<10;i++){await delay(100);flight.push(await evaluate(state));}
 await key('KeyW','w','keyUp');await key('ShiftLeft','Shift','keyUp');
 const stills=[];
 try {
  for(const heading of [0,90,180,270]) {
   await evaluate(`window.__game.orbit.yaw=${-heading*Math.PI/180}`);
   const started=Date.now();let row;
   do {await delay(500);row=await evaluate(state);if(Date.now()-started>timeout)throw Error('Flight coverage timeout: '+JSON.stringify(row.coverage));}while(!row.coverage.ready);
   if(!row.fly||row.camera[1]<100||row.camera[1]>150||!row.groundOwners.length)throw Error('Invalid native flight evidence');
   const file=`flight-${heading}.jpg`,shot=await send('Page.captureScreenshot',{format:'jpeg',quality:92,captureBeyondViewport:false});
   fs.writeFileSync(path.join(out,file),Buffer.from(shot.data,'base64'));stills.push({file,heading,...row});
   fs.writeFileSync(path.join(out,'flight.json'),JSON.stringify({flight,stills},null,1));
   console.log(JSON.stringify({file,fly:row.fly,camera:row.camera,groundOwners:row.groundOwners,coverage:row.coverage.ready}));
  }
 }finally{await key('KeyW','w','keyUp');await key('ShiftLeft','Shift','keyUp');await evaluate('window.__render.beforeFrame=()=>false');await send('Page.setWebLifecycleState',{state:'frozen'});}
}
