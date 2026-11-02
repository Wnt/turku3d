import * as T from 'three/webgpu';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TruckVisual } from '../actors/truck';
import { TRUCK } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';

const scene=new T.Scene();scene.background=new T.Color(0x728895);scene.fog=new T.Fog(0x728895,35,95);
const camera=new T.PerspectiveCamera(38,innerWidth/innerHeight,.05,200);camera.position.set(7,3.8,-7.4);
const renderer=new T.WebGPURenderer({antialias:true,forceWebGL:location.search.includes('webgl')});renderer.setSize(innerWidth,innerHeight);renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
renderer.toneMapping=T.AgXToneMapping;renderer.toneMappingExposure=.9;renderer.shadowMap.enabled=true;
document.body.appendChild(renderer.domElement);await renderer.init();

scene.add(new T.HemisphereLight(0xc6e2ff,0x554735,1));
const sun=new T.DirectionalLight(0xffe0a7,3.3);sun.position.set(6,10,-7);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
Object.assign(sun.shadow.camera,{left:-8,right:8,top:8,bottom:-8,near:.1,far:35});sun.shadow.normalBias=.025;sun.shadow.bias=-.0002;scene.add(sun);
const ground=new T.Mesh(new T.PlaneGeometry(200,200),new T.MeshStandardMaterial({color:0x4c514a,roughness:1}));ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,.9,0);controls.enableDamping=true;controls.maxDistance=140;controls.minDistance=2;controls.maxPolarAngle=Math.PI*.49;
let truck=new TruckVisual(scene);await truck.load();
const staticState=():VehicleState=>({kind:'truck',pos:{x:0,y:TRUCK.wheelRadius+TRUCK.rest-TRUCK.wheels[0].y-9.81/(4*TRUCK.suspensionStiffness),z:0},rot:{x:0,y:0,z:0,w:1},linvel:{x:0,y:0,z:0},speed:0,forwardSpeed:0,impact:null,curbStrike:null,bump:null,
 wheels:TRUCK.wheels.map(p=>({pos:{x:p.x,y:p.y-TRUCK.rest+9.81/(4*TRUCK.suspensionStiffness),z:p.z},steer:0,rotation:0,contact:true,suspension:TRUCK.rest}))});
let state=staticState();truck.update(state);
await renderer.compileAsync(scene,camera);
let drive:ReturnType<typeof import('./prerunnerCourse').prerunnerCourse>|undefined;
let driveCamera:T.Vector3|undefined;
let orbit=false,articulation=false,frames:number[]=[],last=performance.now();
const api={scene,camera,renderer,controls,restState:staticState,
 drive:async()=>{drive?.dispose();drive=(await import('./prerunnerCourse')).prerunnerCourse(scene);orbit=articulation=false;driveCamera=undefined;},
 stopDrive:()=>{drive?.dispose();drive=undefined;state=staticState();truck.update(state);controls.target.set(0,.9,0);api.pose(-.8,.29,8.8);},
 driveStats:()=>drive?.snapshot(),
 capture:async(width=1600,height=1000)=>{
  const target=new T.RenderTarget(width,height);const aspect=camera.aspect;
  camera.aspect=width/height;camera.updateProjectionMatrix();renderer.setOutputRenderTarget(target);
  await renderer.renderAsync(scene,camera);
  const pixels=await renderer.readRenderTargetPixelsAsync(target,0,0,width,height);
  renderer.setOutputRenderTarget(null);camera.aspect=aspect;camera.updateProjectionMatrix();target.dispose();
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  const data=new ImageData(width,height),bytes=new Uint8Array(pixels.buffer);
  const flipped=(renderer.backend as unknown as {isWebGLBackend:boolean}).isWebGLBackend;
  for(let y=0;y<height;y++){const row=flipped?height-y-1:y;data.data.set(bytes.subarray(row*width*4,(row+1)*width*4),y*width*4);}
  canvas.getContext('2d')!.putImageData(data,0,0);return canvas.toDataURL('image/png');
 },get truck(){return truck;},pose:(yaw:number,pitch=.34,distance=9)=>{camera.position.set(Math.cos(yaw)*Math.cos(pitch)*distance,.9+Math.sin(pitch)*distance,Math.sin(yaw)*Math.cos(pitch)*distance);controls.update();},orbit:(on=true)=>orbit=on,articulate:(on=true)=>articulation=on,
 snapshot:()=>({fps:1000/(frames.reduce((a,b)=>a+b,0)/frames.length),calls:renderer.info.render.drawCalls,triangles:renderer.info.render.triangles,geometry:renderer.info.memory.geometries}),setState:(s:VehicleState)=>{state=s;truck.update(s);}};
Object.assign(window,{__prerunner:api,__dev:{prerunner:api}});
renderer.setAnimationLoop(()=>{
 const now=performance.now(),dt=now-last;last=now;frames.push(dt);if(frames.length>180)frames.shift();
 if(orbit){const a=now*.00015;api.pose(a,.28,8.5);}
 if(articulation){const s=staticState();s.wheels.forEach((w,i)=>w.pos.y+=Math.sin(now*.0015+i*Math.PI*.5)*.18);truck.update(s);}
 if(drive){state=drive.step(dt/1000);truck.update(state);const p=new T.Vector3(state.pos.x,state.pos.y+.1,state.pos.z);if(!driveCamera){driveCamera=p.clone();camera.position.copy(p).add(new T.Vector3(5,2.6,-8));}else camera.position.add(p.clone().sub(driveCamera));controls.target.copy(p);driveCamera.copy(p);}
 controls.update();renderer.render(scene,camera);
 document.getElementById('stats')!.textContent=`${api.snapshot().fps.toFixed(0)} FPS  /  ${renderer.info.render.drawCalls} DRAWS  /  ${Math.round(renderer.info.render.triangles/1000)}K TRIANGLES`;
});
if(import.meta.hot)import.meta.hot.accept('../actors/truck',async module=>{if(!module)return;truck.dispose();truck=new module.TruckVisual(scene);await truck.load();state=staticState();truck.update(state);});
