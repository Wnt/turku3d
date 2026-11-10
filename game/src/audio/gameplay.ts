import type { V3 } from '../core/origin';
import type { CharacterState, VehicleInput, VehicleState } from '../physics/protocol';
import { VEHICLES, type VehicleKind } from '../physics/constants';
import { mechanicsOf } from '../vehicle/mechanics';
import { clamp, type AudioEvent } from './mix';
import { TruckSoundFeed } from './engine/feed';
import { SILENT_TYRES, TyreTracker } from './engine/tyre';
import type { EngineDrive } from './engine/drivetrain';

/** Converts game snapshots into audio events once per physics tick, independent of render FPS. */
/** The car and scooter have no drivetrain model: a three-gear approximation provides the car's audible rev range;
 *  the scooter's continuously variable transmission holds high revs that climb with speed. The trophy truck's revs
 *  come from its drivetrain model (engine/drivetrain.ts) through TruckSoundFeed. */
export function engineRpm(kind: VehicleKind, speed: number, throttle: number) {
  if (kind === 'scooter') return 1800 + throttle * 4200 + speed * 160;
  const gear = speed < 8 ? 1 : speed < 17 ? 2 : 3;
  return 850 + speed / gear * 300 + throttle * 700;
}

export class GameplayAudio {
  private tick = -1;
  private tyres = new TyreTracker('car');
  private tyreKind: VehicleKind = 'car';
  private tyreTick = -1;
  private tyreFrame = SILENT_TYRES;
  private distance = 0;
  private previousFeet: V3 | null = null;
  private mode: 'foot' | 'car' = 'foot';
  private curbId = 0;
  private impactId = 0;
  private connected = false;
  private closeAt = 0;
  private doorPosition: V3 | null = null;
  private truckFeed: TruckSoundFeed | null = null;
  private truckId: string | null = null;
  private truckTick = -1;
  /** The local truck's latest drivetrain state (HUD, haptics, and the `drv` wire record: net/drv.ts drvOf). */
  truckDrive: EngineDrive | null = null;

  constructor(private readonly emit: (event: AudioEvent) => void) {}

  /** The local truck's engine voice: the drivetrain model steps once per physics tick from the driver's input and
   *  the wheel state, and the voice gets one frame. Returns the rpm (for the shared `car` event). */
  private truck(tick: number, v: VehicleState, input: VehicleInput | null, throttle: number, driving: boolean) {
    if (!driving) return this.truckDrive?.rpm ?? 0;
    const id = v.id ?? 'truck';
    if (id !== this.truckId) { if (this.truckId) this.emit({ type: 'engine-voice', id: this.truckId, state: null }); this.truckId = id; this.truckFeed = new TruckSoundFeed(7); this.truckTick = -1; }
    const steps = this.truckTick < 0 ? 1 : tick - this.truckTick;
    if (steps > 0 || !this.truckDrive) {
      const reverse = v.forwardSpeed < -0.5 || (Math.abs(v.forwardSpeed) < 1 && (input?.brake ?? 0) > 0.1 && (input?.throttle ?? 0) <= 0.05);
      const out = this.truckFeed!.step(Math.max(1, steps) / 60, v, { throttle, brake: input?.brake ?? 0, reverse });
      this.truckTick = tick; this.truckDrive = out.drive;
      this.emit({ type: 'engine-voice', id, state: { drive: out.drive, speed: v.speed, position: v.pos, velocity: v.linvel,
        local: true, wheels: v.wheels, rearSlew: out.rearSlew } });
    }
    return this.truckDrive!.rpm;
  }

  update(frame: { tick: number; time: number; paused: boolean; mode: 'foot' | 'car'; connected: boolean;
    character: CharacterState | null; vehicle: VehicleState | null; input: VehicleInput | null;
    position: V3; forward: V3; up: V3 }) {
    const { character: c, vehicle: v, input, mode } = frame;
    this.emit({ type: 'listener', position: frame.position, forward: frame.forward, up: frame.up, paused: frame.paused });
    if (frame.connected && !this.connected) this.emit({ type: 'gamepad-connected' });
    this.connected = frame.connected;
    // Car doors open and close on entry and exit; a scooter has none.
    if (mode !== this.mode && v && v.kind !== 'scooter') {
      this.emit({ type: 'door', position: v.pos, open: true });
      this.doorPosition = { ...v.pos }; this.closeAt = frame.time + 0.3;
      this.previousFeet = null; this.distance = 0;
    }
    this.mode = mode;
    if (this.doorPosition && frame.time >= this.closeAt) {
      this.emit({ type: 'door', position: v?.pos ?? this.doorPosition, open: false }); this.doorPosition = null;
    }
    if (v) {
      const kind = v.kind ?? 'car';
      // The tyre frame once per physics tick: every tick's slide reaches the synthesiser, so a short chirp is heard.
      if (kind !== this.tyreKind) { this.tyreKind = kind; this.tyres = new TyreTracker(kind); this.tyreTick = -1; }
      if (!frame.paused && frame.tick !== this.tyreTick) {
        const dt = this.tyreTick < 0 ? 1 / 60 : (frame.tick - this.tyreTick) / 60;
        this.tyreTick = frame.tick;
        this.tyreFrame = this.tyres.step(dt, v.wheels, v.speed);
      }
      // An undriven car still rolls audibly, but only a driven one squeals.
      const tyres = mode === 'car' ? this.tyreFrame : { ...this.tyreFrame, slide: 0 };
      const contact = v.wheels.some(w => w.contact);
      const throttle = clamp(Math.abs(input?.throttle ?? 0) + (v.forwardSpeed < 0 ? input?.brake ?? 0 : 0));
      const rpm = kind === 'truck' ? this.truck(frame.tick, v, input, throttle, mode === 'car') : engineRpm(kind, Math.abs(v.forwardSpeed), throttle);
      const { rough, steam, loose } = mechanicsOf(v.damage);
      this.emit({ type: 'car', kind, position: v.pos, rpm,
        throttle, speed: contact ? v.speed : 0, tyres,
        running: mode === 'car', ...(rough ? { rough } : {}), ...(steam ? { steam } : {}), ...(loose ? { loose } : {}),
        ...(v.scrape ? { scrape: v.scrape } : {}) });
    }
    if ((!v || v.kind !== 'truck' || mode !== 'car') && this.truckId) {
      this.emit({ type: 'engine-voice', id: this.truckId, state: null });
      this.truckId = null; this.truckFeed = null; this.truckDrive = null; this.truckTick = -1;
    }
    if (!v) { this.tyreTick = -1; this.tyreFrame = SILENT_TYRES; }
    if (frame.paused || frame.tick === this.tick) return;
    this.tick = frame.tick;
    if (c?.enabled && c.grounded && mode === 'foot') {
      if (this.previousFeet) {
        const d = Math.hypot(c.pos.x - this.previousFeet.x, c.pos.z - this.previousFeet.z);
        if (d < 2) this.distance += d; else this.distance = 0; // teleports never produce a burst
      }
      this.previousFeet = { ...c.pos };
      const speed = Math.hypot(c.vel.x, c.vel.z), stride = speed > 4 ? 1.35 : 0.85;
      if (this.distance >= stride && speed > 0.15) {
        this.distance %= stride;
        this.emit({ type: 'footstep', material: c.groundMaterial, position: c.pos, speed });
      }
    } else { this.previousFeet = null; this.distance = 0; }
    if (v?.curbStrike && v.curbStrike.id !== this.curbId) {
      this.curbId = v.curbStrike.id;
      this.emit({ type: 'curb', position: v.pos, impulse: v.curbStrike.speed * VEHICLES[v.kind ?? 'car'].mass * v.curbStrike.loss });
    }
    if (v?.impact && v.impact.id !== this.impactId) {
      this.impactId = v.impact.id;
      this.emit({ type: 'impact', position: v.pos, impulse: v.impact.impulse });
    }
  }
}
