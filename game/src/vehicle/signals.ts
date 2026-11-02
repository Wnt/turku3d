/** Absolute states cross the worker/network boundary; edges are consumed only by the input owner. */
export type LightState = 0 | 1 | 2;
export const LIGHT_LABELS = ['Lights off', 'Driving lights', 'Long beams'] as const;
export class VehicleSignals {
  lights: LightState = 0;
  horn = false;
  enter() { this.lights = 1; this.horn = false; }
  update(active: boolean, cycle: boolean, held: boolean) {
    if (active && cycle) this.lights = ((this.lights + 1) % 3) as LightState;
    this.horn = active && held;
  }
}
