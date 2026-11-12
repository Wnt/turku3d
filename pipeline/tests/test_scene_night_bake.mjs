import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apertureSamples, bake } from '../turku3d/scene/night_bake.mjs';

const point = { id: 'lamp', position: [0,4,0], color: [1,.5,.25], intensity: 45, range: 100 };
const receiver = (id, p) => ({ id, materialId: 'stone', positions: [...p,p[0]+.01,p[1],p[2],p[0],p[1],p[2]+.01],
  normals: [0,1,0,0,1,0,0,1,0], indices: [0,2,1], collision: 'passable' });
const floor = (id, y) => ({ id, render: false, collision: 'solid', positions: [-10,y,-10,-10,y,10,10,y,10,10,y,-10], indices: [0,1,2,0,2,3] });
const scene = (meshes, emitters=[point]) => ({ meshes, emitters, materials: [{ id:'stone' }] });
const value = (s, options) => bake(s,options).values.floor[0];
const close = (a,b,eps=1e-6) => assert.ok(Math.abs(a-b) < eps, `${a} != ${b}`);

test('point output matches Lambert/range/color and actual upper geometry blocks only the lower receiver', () => {
  const meshes=[receiver('floor',[0,0,0]),receiver('upper',[0,3,0])];
  const open=bake(scene(meshes));
  close(open.values.floor[0],45/(16*Math.PI)*(1-(16/10000)**2)**2);
  close(open.values.floor[1],open.values.floor[0]*.5);
  const blocked=bake(scene([...meshes,floor('bridge',2)]));
  assert.equal(blocked.values.floor[0],0);
  close(blocked.values.upper[0],open.values.upper[0]);
});

test('explicit optical core replaces a broader walking hull without removing other blockers', () => {
  const leaf=receiver('floor',[0,1,0]);
  const hull={...floor('walking-hull',2),lightOcclusion:'none'};
  const core={...floor('visible-core',.5),collision:'passable',lightOcclusion:'solid'};
  const open=value(scene([leaf]));
  close(value(scene([leaf,hull,core])),open);
  assert.equal(value(scene([leaf,hull,core,floor('other-wall',3)])),0);
  const behind=receiver('floor',[0,0,0]);
  assert.equal(value(scene([behind,hull,core])),0);
  assert.equal(bake(scene([leaf,hull,core])).audit.triangles,2);
});

test('finite aperture conserves its on-axis intensity as quadrature count changes and rejects upward emission', () => {
  const aperture={ shape:'rectangle',position:[0,4,0],normal:[0,-1,0],axisU:[1,0,0],size:[1.2,.84],cutoutRadius:.046 };
  const emitter={...point,aperture};
  for(const resolution of [2,4,5,8]) {
    const samples=apertureSamples(emitter,resolution);
    close(samples.reduce((sum,s)=>sum+s.weight,0),1);
    assert.ok(samples.every(s=>Math.hypot(s.position[0],s.position[2])>=.046));
    const irradiance=value(scene([receiver('floor',[0,-26,0])],[emitter]),{resolution});
    const expected=45/(900*Math.PI)*(1-(900/10000)**2)**2;
    close(irradiance/expected,1,.002);
  }
  emitter.aperture.normal=[0,1,0];
  assert.equal(value(scene([receiver('floor',[0,0,0])],[emitter])),0);
});

test('17 and 100 emitters conserve linear energy without growing geometry or changing visibility', () => {
  const meshes=[receiver('floor',[0,0,0])],one=value(scene(meshes));
  for(const count of [17,100]) {
    const result=bake(scene(meshes,Array.from({length:count},(_,i)=>({...point,id:String(i)}))));
    close(result.values.floor[0],one*count);
    assert.equal(result.audit.rays,3*count);
    assert.equal(result.audit.vertices,3);
  }
});

test('reflected underside illuminates around its real pole while a point inside the pole is blocked', () => {
  const pole={ id:'pole',render:false,collision:'solid',
    positions:[-.046,0,-.046,.046,0,-.046,.046,5.56,-.046,-.046,5.56,-.046,
      -.046,0,.046,.046,0,.046,.046,5.56,.046,-.046,5.56,.046],
    indices:[0,2,1,0,3,2,4,5,6,4,6,7,0,4,7,0,7,3,1,2,6,1,6,5,3,7,6,3,6,2] };
  const emitter={ ...point,position:[0,4.85,0] };
  const meshes=[receiver('floor',[2,0,0]),pole,floor('reflector',5.56)];
  assert.equal(value(scene(meshes,[emitter])),0);
  emitter.aperture={ shape:'rectangle',position:[0,5.555,0],normal:[0,-1,0],axisU:[1,0,0],size:[1.2,.84],cutoutRadius:.046 };
  assert.ok(value(scene(meshes,[emitter]))>.1);
});
