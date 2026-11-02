import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {poseAt,tilesForShot,allTiles} from './paths.mjs';
const config=JSON.parse(fs.readFileSync(new URL('./comparison-shots.json',import.meta.url)));
test('all shots fit a frame-exact one-minute timeline',()=>{
 assert.equal(config.shots.length,15);
 const seconds=config.shots.length*config.duration+config.titleSeconds+config.endSeconds-(config.shots.length+1)*config.transition;
 assert.ok(Math.abs(seconds-60)<1e-9);
 for(const d of [config.duration,config.titleSeconds,config.endSeconds,config.transition])assert.equal(d*config.fps,Math.round(d*config.fps));
});
test('camera interpolation preserves endpoints and takes the short yaw path',()=>{
 const shot={from:[23459500,6704500,10,359,0,60],to:[23459751,6704500,10,1,0,60]};
 assert.equal(poseAt(shot,0).E,shot.from[0]);assert.equal(poseAt(shot,1).E,shot.to[0]);assert.equal(poseAt(shot,.5).yaw,360);
 const tiles=tilesForShot(shot);for(const id of ['t0_-1_-1','t0_0_0','t0_1_0','t0_2_1'])assert.ok(tiles.includes(id),id);
});
test('every sampled path has a complete one-tile ring, including negative indices',()=>{
 for(const shot of config.shots){const tiles=new Set(tilesForShot(shot));for(let f=0;f<240;f++){
  const p=poseAt(shot,f/239),x=Math.floor((p.E-23459500)/250),z=Math.floor((6704500-p.N)/250);
  for(let i=-1;i<=1;i++)for(let j=-1;j<=1;j++)assert.ok(tiles.has(`t0_${x+i}_${z+j}`));
 }}assert.equal(new Set(allTiles(config)).size,allTiles(config).length);
});
