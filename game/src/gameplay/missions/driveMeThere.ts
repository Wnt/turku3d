// "Drive me there": the mission hook of the autopilot (gameplay/autopilot). It drives the player's vehicle, or
// walks the player, to the active mission target (the one the minimap routes to): the pizzeria, the shop, the
// current drop-off or the heist yard. Missions use it for a taxi-style hand-over or a short scripted drive; the
// dev console (`__game.driveMeThere()`) and `?autopilot=target` trigger it too. Touching the stick or a pedal
// takes the wheel back. Multiplayer: each client drives only its own player toward its own mission target.
import type { MissionHudState, RouteTarget } from '../contract';
import type { Autopilot, AutopilotHandle, GoToOptions } from '../autopilot';

export interface DriveMeThereSource { subscribe(listener: (hud: MissionHudState) => void): () => void }

/** The target the minimap routes to, or null when no mission is active. */
export function activeTarget(hud: MissionHudState | null): RouteTarget | null {
  if (!hud?.activeTargetId) return null;
  return hud.targets.find(t => t.id === hud.activeTargetId) ?? null;
}

export class DriveMeThere {
  /** The running hand-over and its target id. */
  trip: AutopilotHandle | null = null;
  target: string | null = null;

  private hud: MissionHudState | null = null;
  readonly dispose: () => void;

  constructor(missions: DriveMeThereSource, private readonly autopilot: Autopilot) {
    this.dispose = missions.subscribe(hud => {
      this.hud = hud;
      // The target changed under a running hand-over (a drop-off delivered, a job cancelled): stop driving.
      if (this.trip && this.target && activeTarget(hud)?.id !== this.target) this.trip.cancel('mission target changed');
    });
  }

  /** Starts the hand-over to the active target; null when there is none. Arrival leaves the player in the car (or
   *  on foot) at the target's approach point; the mission itself still asks for the usual interaction. */
  start(options: GoToOptions = {}): AutopilotHandle | null {
    const target = activeTarget(this.hud);
    if (!target) return null;
    const point = target.approach ?? target.point;
    const trip = this.autopilot.goTo({ x: point.x, z: point.z, ...(point.y !== undefined ? { y: point.y } : {}) },
      { style: 'normal', arrive: 4, ...options });
    this.trip = trip; this.target = target.id;
    void trip.done.then(result => {
      if (this.trip !== trip) return;
      this.trip = null; this.target = null;
      console.info(`[autopilot] drive me there: ${target.label} ${result.state}${result.reason ? ` (${result.reason})` : ''}`);
    });
    return trip;
  }
}
