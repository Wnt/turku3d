import type { FleetModel } from './catalog';

/** Shared body/track profile used by merged geometry and independently animated wheels. */
export function fleetHalfWidth(model:FleetModel,z:number){
  const corner=model.body==='classic'?.045:model.body==='van'?.055:[.09,.075,.115,.065,.10,.085,.055][model.face];
  return model.width/2*(1-corner*Math.pow(Math.abs(2*z/model.length),model.body==='classic'?8:model.body==='van'?10:4+model.face%3));
}
export function fleetWheelCenter(model:FleetModel,front:boolean,side:number):[number,number,number]{
  const z=(front?1:-1)*model.wheelbase/2;
  return [side*(fleetHalfWidth(model,z)-.132),model.wheel,z];
}

/** Physics uses +X forward and -Z left; the authored model uses +Z forward. */
export function fleetWheelGeometry(model:FleetModel){
  return {wheelbase:model.wheelbase,track:2*(fleetHalfWidth(model,model.wheelbase/2)-.132),radius:model.wheel};
}
