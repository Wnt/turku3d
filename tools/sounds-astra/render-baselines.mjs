// Render the checked-out game code without changing it or opening the game.
// Run inside the host-lease session; OfflineAudioContext uses its one Chrome.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const root = process.cwd();
const work = process.env.SOUND_WORK || `${process.env.HOME}/turku3d/loops/sounds-astra`;
const { build } = await import(pathToFileURL(`${work}/work/node/node_modules/esbuild/lib/main.js`));
const { chromium } = await import(pathToFileURL(`${work}/work/node/node_modules/playwright/index.mjs`));
mkdirSync(`${work}/work/baseline`, { recursive: true });
const entry = `import {MusicSystem, TRACKS, hurry} from '${root}/game/src/audio/music/index.ts';
import {ChassisSynth, SILENT_FRAME} from '${root}/game/src/audio/engine/chassis.ts';
window.renderBaseline = async (trackIndex, scene, bars) => {
 const track=TRACKS[trackIndex], seconds=bars*4*60/track.bpm+2;
 const context=new OfflineAudioContext(1,Math.ceil(seconds*48000),48000);
 const music=new MusicSystem(context); music.setMuffled(0);
 const curve=hurry(scene==='mission' ? .85 : 0, .65, .18);
 for(let beat=0;beat<bars*4;beat++) music.scheduleBeat(track,scene,beat,beat*60/track.bpm,1,curve);
 const result=await context.startRendering();
 return {samples:Array.from(result.getChannelData(0)),seconds,track:track.id,scene};
};
window.renderTruck=()=> {const voice=new ChassisSynth(41,48000); voice.frame({...SILENT_FRAME,screech:0.8});
const out=new Float32Array(48000*10),block=new Float32Array(128);
for(let i=0;i<out.length;i+=128){block.fill(0);voice.render(block);out.set(block.subarray(0,Math.min(128,out.length-i)),i);}
return Array.from(out);};`;
const bundled = await build({stdin:{contents:entry,resolveDir:root,loader:'ts'}, bundle:true, write:false, format:'iife'});
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const page = await browser.contexts()[0].newPage();
await page.goto('about:blank');
await page.addScriptTag({content:bundled.outputFiles[0].text});
const manifest=[];
for (let track=0;track<5;track++) {
 const r=await page.evaluate(async i=>await window.renderBaseline(i,'explore',16),track);
 const id=`current-music-${r.track}`;
 writeFileSync(`${work}/work/baseline/${id}.f32`,Buffer.from(new Float32Array(r.samples).buffer));
 manifest.push({id,seconds:r.seconds,scene:r.scene,track:r.track});
 console.log(`Rendered ${id}: ${r.seconds.toFixed(2)}s`);
}
const mission=await page.evaluate(()=>window.renderBaseline(0,'mission',16));
writeFileSync(`${work}/work/baseline/current-music-mission.f32`,Buffer.from(new Float32Array(mission.samples).buffer));
manifest.push({id:'current-music-mission',seconds:mission.seconds,scene:'mission',track:'aura-neon',note:'Fixed BPM; deadline=.85 timbre, isolated sequencer, no game bus or driving muffle.'});
const truck=await page.evaluate(()=>window.renderTruck());
writeFileSync(`${work}/work/baseline/current-truck.f32`,Buffer.from(new Float32Array(truck).buffer));
writeFileSync(`${work}/evidence/baseline-renders.json`,JSON.stringify(manifest,null,2));
await page.close();
await browser.close();
