import * as T from 'three/webgpu';
import { createRacerArtwork } from './artwork';

let cached: ReturnType<typeof create> | undefined;
/** All original painted imagery, generated once; no downloaded artwork or real sponsor marks. */
function create() {
  const {livery,dirt,netMap}=createRacerArtwork();
  const net=new T.MeshStandardMaterial({name:'window-safety-webbing',color:0x777c74,map:netMap,alphaTest:.5,roughness:.95,side:T.DoubleSide});
  const make=(name:string,color:number,roughness:number,metalness=0)=>{const m=new T.MeshStandardMaterial({color,roughness,metalness});m.name=name;return m;};
  const paint=new T.MeshPhysicalMaterial({name:'ceramic-teal',color:0x07545a,roughness:.32,metalness:0,clearcoat:.8,clearcoatRoughness:.24,side:T.DoubleSide});
  const frame=make('powdercoat-charcoal',0x17252a,.48,0);
  const alloy=make('machined-alloy',0xb3bcb9,.28,.86);
  const cast=make('cast-engine-aluminium',0x747f7e,.64,.82);
  const shaft=make('polished-damper-shaft',0xcbd1cf,.16,.95);
  const exhaust=make('heat-aged-stainless',0x897d6a,.54,.88);
  const orange=make('safety-orange',0xe55717,.34,0);
  const rubber=make('terrain-rubber',0x626564,.95);rubber.map=dirt;rubber.userData.distantColor=0x202320;
  const black=make('cabin-carbon',0x101518,.88);
  const mud=make('dry-granite-dust',0x71634b,.96);
  const red=make('tail-lens',0x8d1008,.25,.12);red.emissive.set(0xc92b0d);red.emissiveIntensity=.3;
  const lamp=make('led-optics',0xede8d2,.18,.15);lamp.emissive.set(0xffe9c4);lamp.emissiveIntensity=1.4;
  const decal=make('race-livery',0xffffff,.53,0);decal.map=livery;decal.userData.distantColor=0x3c6260;
  const distant=new T.MeshStandardMaterial({name:'korpi-distance',vertexColors:true,roughness:.75,metalness:.05,side:T.DoubleSide});
  return {paint,frame,alloy,orange,rubber,black,mud,red,lamp,decal,distant,net,cast,shaft,exhaust};
}
export function racerMaterials(){return cached??=create();}

if(import.meta.hot)import.meta.hot.dispose(()=>{
  if(!cached)return;
  const textures=new Set<T.Texture>();
  for(const material of Object.values(cached)){
    for(const value of Object.values(material))if(value?.isTexture)textures.add(value as T.Texture);
    material.dispose();
  }
  for(const texture of textures)texture.dispose();cached=undefined;
});
