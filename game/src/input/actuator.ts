// Drives whatever vibration a gamepad exposes, by feature detection only (never by controller name): the standard
// `vibrationActuator.playEffect('dual-rumble')` (Chrome, Edge, Safari: Xbox, DualSense, DualShock 4, Switch Pro and
// generic XInput/HID pads the browser can vibrate), else the older `hapticActuators[i].pulse()` (Firefox), else
// nothing. README "Haptics" lists how each controller type ends up driven.
import { clamp } from './bindings';

interface EffectActuator {
  /** Chrome 126+ lists the playable effects; older versions only have `type`. */
  effects?: readonly string[];
  type?: string;
  /** Safari's name for the same question. */
  canPlayEffectType?(type: string): boolean;
  playEffect?(type: string, params: Record<string, number>): Promise<unknown>;
  reset?(): Promise<unknown>;
}
interface PulseActuator { pulse?(value: number, duration: number): Promise<unknown> }
type RumblePad = { vibrationActuator?: EffectActuator | null; hapticActuators?: readonly (PulseActuator | null)[] | null };

export type RumbleDriver = 'dual-rumble' | 'pulse' | 'none';

function dualRumble(pad: RumblePad): EffectActuator | null {
  const a = pad.vibrationActuator;
  if (!a || typeof a.playEffect !== 'function') return null;
  if (Array.isArray(a.effects)) return a.effects.includes('dual-rumble') ? a : null;
  if (typeof a.canPlayEffectType === 'function') return a.canPlayEffectType('dual-rumble') ? a : null;
  // No capability list: `type` names the one effect, and an actuator without either is tried (it rejects if not).
  return a.type === undefined || a.type === 'dual-rumble' ? a : null;
}
const pulsers = (pad: RumblePad) =>
  Array.from(pad.hapticActuators ?? []).filter((a): a is Required<PulseActuator> => typeof a?.pulse === 'function');

/** How `pad` can be vibrated. */
export function rumbleDriver(pad: RumblePad | null | undefined): RumbleDriver {
  if (!pad) return 'none';
  return dualRumble(pad) ? 'dual-rumble' : pulsers(pad).length ? 'pulse' : 'none';
}

/** Plays one effect on `pad` (duration ms, magnitudes 0..1; strong = low-frequency motor, weak = high-frequency).
 *  It replaces the effect still playing. Resolves false when the pad cannot vibrate or the effect was refused. */
export async function playRumble(pad: RumblePad | null | undefined, duration: number, strong: number, weak: number): Promise<boolean> {
  if (!pad) return false;
  const ms = clamp(duration, 0, 1000), s = clamp(strong, 0, 1), w = clamp(weak, 0, 1);
  try {
    const effect = dualRumble(pad);
    if (effect) {
      const result = await effect.playEffect!('dual-rumble', { duration: ms, startDelay: 0, strongMagnitude: s, weakMagnitude: w });
      // A newer effect replacing this one ("preempted") is the normal end of an overlapping pulse.
      return result === 'complete' || result === 'preempted';
    }
    const list = pulsers(pad);
    if (!list.length) return false;
    // Two actuators are the strong and the weak motor; a single one gets the stronger of the two magnitudes.
    await Promise.all(list.length === 1 ? [list[0].pulse(Math.max(s, w), ms)]
      : [list[0].pulse(s, ms), list[1].pulse(w, ms)]);
    return true;
  } catch { return false; }
}

/** Stops `pad`'s vibration now. */
export async function stopRumble(pad: RumblePad | null | undefined): Promise<void> {
  if (!pad) return;
  try {
    const effect = dualRumble(pad);
    if (effect?.reset) await effect.reset();
    else if (effect) await effect.playEffect!('dual-rumble', { duration: 1, startDelay: 0, strongMagnitude: 0, weakMagnitude: 0 });
    else await Promise.all(pulsers(pad).map(a => a.pulse(0, 1)));
  } catch { /* a pad that went away has nothing left to stop */ }
}
