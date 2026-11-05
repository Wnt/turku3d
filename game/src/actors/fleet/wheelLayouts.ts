import {AUTHORED_BY_ID} from './registry';
export const fleetWheelLayout=(id:string)=>AUTHORED_BY_ID.get(id)?.wheels;

const footprints=new Map<string,import('../../vehicle/wheelGround').WheelFootprint>();
export function fleetWheelFootprint(model:import('./catalog').FleetModel) {
  let footprint=footprints.get(model.id);if(footprint)return footprint;
  const layout=fleetWheelLayout(model.id);
  footprint={wheelbase:model.wheelbase,track:model.width-.15,radius:layout?.radius??model.wheel,
    hubs:layout?Object.values(layout.pivots).map(p=>({x:p[2],z:-p[0]})).sort((a,b)=>b.x-a.x||a.z-b.z):undefined};
  footprints.set(model.id,footprint);return footprint;
}
