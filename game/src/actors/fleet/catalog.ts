/** Original fictional vehicles. Dimensions describe visual bodywork, in metres.
 * The existing parking/traffic record remains the authority for collision and placement. */
export type BodyType = 'hatch' | 'estate' | 'sedan' | 'suv' | 'mpv' | 'van' | 'pickup' | 'classic';
export interface FleetModel {
  id: string; name: string; body: BodyType; family: string;
  length: number; width: number; height: number; wheelbase: number;
  hood: number; rear: number; taper: number; wheel: number; belt: number;
  face: number; accessory: 'none' | 'box' | 'rack' | 'taxi'; age: number;
}
// Seven design families per body class: each has its own proportions, glazing and fascia.
// These are visual art direction, not a claim to replicate a manufacturer's design or sales shares.
const brands = ['Aava', 'Kaira', 'Nord', 'Vire', 'Salo', 'Valo', 'Routa'];
const types: [BodyType, string, string, number, number, number, number, number, number][] = [
  ['hatch', 'Kipinä', 'hatchback-sports', 4.08, 1.77, 1.47, 2.57, .28, -.39],
  ['estate', 'Virta', 'sedan', 4.68, 1.83, 1.49, 2.77, .24, -.43],
  ['sedan', 'Säde', 'sedan', 4.57, 1.82, 1.46, 2.72, .24, -.29],
  ['suv', 'Vaara', 'suv', 4.53, 1.90, 1.72, 2.68, .25, -.42],
  ['mpv', 'Perhe', 'van', 4.59, 1.85, 1.78, 2.77, .30, -.43],
  ['van', 'Kuorma', 'van', 5.03, 1.98, 2.08, 3.02, .35, -.46],
  ['pickup', 'Louhi', 'suv', 5.15, 1.94, 1.81, 3.10, .25, -.17],
  ['classic', 'Muisto', 'sedan', 4.44, 1.76, 1.43, 2.61, .19, -.26],
];
export const FLEET: readonly FleetModel[] = types.flatMap(([body, name, family, l, w, h, wb, hood, rear], t) =>
  brands.map((brand, i) => ({ id: `fleet-${body}-${i}`, name: `${brand} ${name} ${110 + i * 20}`, body, family,
    length: l + [-.18, .10, .24, -.08, .02, .17, -.24][i], width: w + [-.02, .01, .05, -.04, 0, .03, -.01][i],
    height: h + [.025, -.02, .045, .015, -.035, .055, 0][i], wheelbase: wb + [-.09, .02, .12, -.03, .04, .08, -.11][i],
    hood: hood + [.01, -.02, -.015, .025, 0, -.01, .035][i], rear: rear + [0, .015, -.01, .025, -.02, .01, 0][i],
    taper: [.82, .78, .84, .86, .80, .83, .88][i], wheel: (body === 'suv' || body === 'pickup' ? .365 : body === 'van' ? .35 : .315) + (i % 3) * .012,
    belt: (body === 'van' ? 1.12 : body === 'suv' || body === 'pickup' ? 1.12 : body === 'mpv' ? 1.10 : .97) + (i % 3) * .025,
    face: i, accessory: t === 1 && i === 1 || t === 3 && i === 2 ? 'box' : t === 1 && i === 4 || t === 3 && i === 5 ? 'rack' : t === 2 && i === 5 || t === 4 && i === 1 ? 'taxi' : 'none',
    age: body === 'classic' ? .75 + i * .03 : i === 6 ? .5 : .06 + i * .04,
  })));
export const FLEET_BY_ID = new Map(FLEET.map(model => [model.id, model]));
export function fleetHash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i++) h = Math.imul(h ^ value.charCodeAt(i), 16777619);
  h = Math.imul(h ^ h >>> 16, 0x7feb352d);
  h = Math.imul(h ^ h >>> 15, 0x846ca68b);
  return (h ^ h >>> 16) >>> 0;
}
export function fleetModel(model: string, seed: string): FleetModel {
  const exact = FLEET_BY_ID.get(model);
  if (exact) return exact;
  const family = model === 'suv-luxury' ? 'suv' : model === 'sedan-sports' ? 'sedan' : model;
  const choices = FLEET.filter(m => m.family === family);
  if(!choices.length)return FLEET[fleetHash(seed)%FLEET.length];
  const weight=(m:FleetModel)=>m.body==='pickup'?.08:m.body==='suv'?.92:m.body==='classic'?.10:m.body==='estate'||m.body==='sedan'?.45:m.body==='mpv'?.4:m.body==='van'?.6:1;
  let pick=fleetHash(seed)/4294967296*choices.reduce((s,m)=>s+weight(m),0);
  for(const m of choices){pick-=weight(m);if(pick<0)return m;}
  return choices[choices.length-1];
}

export interface FleetSize { width:number; height:number; length:number }
export const FLEET_SIZE: Readonly<Record<string,FleetSize>> = {
  sedan:{width:1.85,height:1.48,length:4.55}, 'hatchback-sports':{width:1.80,height:1.46,length:4.15},
  suv:{width:1.96,height:1.78,length:4.70},van:{width:1.98,height:2.05,length:5.00},
  'sedan-sports':{width:1.84,height:1.36,length:4.60},'suv-luxury':{width:1.98,height:1.72,length:4.95},
};
const fitted=new Map<string,FleetModel>();
export function baseFleetId(id:string){return id.replace(/-d\d+-\d+-\d+$/,'');}
/** A dimensional model key travels through the existing network/save identity. Bodywork is
 * regenerated to fit; tyres stay circular instead of inheriting a nonuniform object scale. */
export function fitFleetModel(model:FleetModel,size:FleetSize):FleetModel {
  const w=Math.round(size.width*100),h=Math.round(size.height*100),l=Math.round(size.length*100);
  if(![w,h,l].every(Number.isFinite)||w<120||w>280||h<80||h>350||l<250||l>750)throw new Error('Invalid fleet dimensions');
  const base=FLEET_BY_ID.get(baseFleetId(model.id))??model,key=`${base.id}-d${w}-${h}-${l}`;
  let fit=fitted.get(key);if(fit)return fit;
  const width=w/100-.22,height=h/100,length=l/100-.12,ys=height/base.height;
  fit={...base,id:key,width,height,length,wheelbase:base.wheelbase*length/base.length,
    wheel:base.wheel*Math.min(ys,width/base.width),belt:base.belt*ys};
  if(fitted.size>512)fitted.clear();
  fitted.set(key,fit);return fit;
}
export function fleetAppearance(model:string,seed:string,size?:FleetSize):FleetModel {
  const encoded=/^(fleet-[a-z]+-\d+)-d(\d+)-(\d+)-(\d+)$/.exec(model);
  if(encoded){const base=FLEET_BY_ID.get(encoded[1]);if(base)return fitFleetModel(base,{width:+encoded[2]/100,height:+encoded[3]/100,length:+encoded[4]/100});}
  const base=fleetModel(model,seed);
  return fitFleetModel(base,size??FLEET_SIZE[model]??FLEET_SIZE[base.family]);
}
