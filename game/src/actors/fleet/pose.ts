import type { V3 } from '../../core/origin';
import type { Quat } from '../../physics/protocol';
import { rotate } from '../../vehicle/math';
import { CHASSIS_LIFT } from '../../traffic/constants';

/** The authored tyre-ground origin sits below the chassis in its local frame, including
 * pitch and roll. A world-Y subtraction would slide a car on sloped ground. */
export function fleetOrigin(chassis:V3,rotation:Quat):V3 {
  const offset=rotate({x:0,y:CHASSIS_LIFT,z:0},rotation);
  return {x:chassis.x-offset.x,y:chassis.y-offset.y,z:chassis.z-offset.z};
}
export function fleetChassis(ground:V3,rotation:Quat):V3 {
  const offset=rotate({x:0,y:CHASSIS_LIFT,z:0},rotation);
  return {x:ground.x+offset.x,y:ground.y+offset.y,z:ground.z+offset.z};
}
