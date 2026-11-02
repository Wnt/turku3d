import * as T from 'three/webgpu';

function texture(draw: (c: CanvasRenderingContext2D, w: number, h: number) => void, w = 1024, h = 512) {
  const canvas = document.createElement('canvas'); canvas.width=w; canvas.height=h;
  draw(canvas.getContext('2d')!,w,h);
  const tex = new T.CanvasTexture(canvas); tex.colorSpace=T.SRGBColorSpace; tex.anisotropy=8;
  return tex;
}

/** Authoring source for the original versioned KORPI artwork. */
export function createRacerArtwork(){
  let seed = 440; const random = () => { seed = (Math.imul(seed,1664525)+1013904223)|0; return (seed>>>0)/4294967296; };
  const livery=texture((c,w,h)=>{
    c.fillStyle='#d6e0db';c.fillRect(0,0,w,h);
    c.fillStyle='#063f42';c.beginPath();c.moveTo(0,0);c.lineTo(w*.78,0);c.lineTo(w*.52,h);c.lineTo(0,h);c.fill();
    c.fillStyle='#f06b27';c.beginPath();c.moveTo(w*.62,0);c.lineTo(w*.72,0);c.lineTo(w*.46,h);c.lineTo(w*.36,h);c.fill();
    c.fillStyle='#0b171c';c.fillRect(w*.66,h*.12,w*.31,h*.7);
    c.fillStyle='#f4f2df';c.font='900 214px sans-serif';c.textAlign='center';c.fillText('440',w*.81,h*.60);
    c.textAlign='left';c.font='italic 900 86px sans-serif';c.fillText('KORPI',w*.035,h*.37);
    c.font='bold 25px sans-serif';c.fillText('TURKU  /  FINLAND',w*.04,h*.47);
    c.fillStyle='#fff1d1';c.font='900 27px sans-serif';c.fillText('SISU IS A STATE OF MIND',w*.04,h*.78);
    c.font='bold 18px sans-serif';c.fillText('AURA WORKS  •  KIVI  •  NORD SHOCK',w*.04,h*.9);
    // Adhered road grit and fine stone chips, strongest along the sill.
    for(let i=0;i<2300;i++) {const x=random()*w,y=random()*h,r=.5+random()*3; c.fillStyle=`rgba(98,76,49,${.1+random()*.22})`; if(y>h*(.62+random()*.38))c.fillRect(x,y,r*1.8,r);}
  });
  const dirt=texture((c,w,h)=>{c.fillStyle='#282b29';c.fillRect(0,0,w,h);for(let i=0;i<16000;i++){const k=35+Math.floor(random()*45);c.fillStyle=`rgb(${k},${k},${k-3})`;c.fillRect(random()*w,random()*h,1+random()*3,1+random()*2);}},256,256);
  dirt.wrapS=dirt.wrapT=T.RepeatWrapping;
  const netMap=texture((c,w,h)=>{c.clearRect(0,0,w,h);c.fillStyle='#242a28';for(let x=0;x<w;x+=32)c.fillRect(x,0,5,h);for(let y=0;y<h;y+=32)c.fillRect(0,y,w,5);},256,256);
  const engineLabel=texture((c,w,h)=>{c.fillStyle='#1c282b';c.fillRect(0,0,w,h);c.fillStyle='#d8dcd2';c.fillRect(0,0,w,4);c.fillRect(0,h-4,w,4);c.textAlign='center';c.font='900 57px sans-serif';c.fillText('AURA  V8SC',w/2,h*.58);c.font='bold 18px sans-serif';c.fillText('SUPERCHARGED  /  TURKU',w/2,h*.85);},512,128);
  const plate=texture((c,w,h)=>{c.fillStyle='#efefe5';c.fillRect(0,0,w,h);c.fillStyle='#12447a';c.fillRect(0,0,w*.115,h);c.fillStyle='#fff';c.textAlign='center';c.font='bold 20px sans-serif';c.fillText('FI',w*.057,h*.65);c.fillStyle='#172126';c.font='900 58px monospace';c.fillText('AUR-440',w*.56,h*.76);},384,80);
  return {livery,dirt,netMap,engineLabel,plate};
}
