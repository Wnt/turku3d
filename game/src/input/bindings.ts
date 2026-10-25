/** Serializable per-action bindings. Standard Gamepad layout, independent of device name. */
export type Binding =
  | { kind: 'axis'; index: number; scale?: number }
  | { kind: 'button'; index: number }
  | { kind: 'key'; code: string; scale?: number };
export const ACTIONS = ['moveX', 'moveY', 'lookX', 'lookY', 'sprint', 'walk', 'jump', 'interact',
  'throttle', 'brake', 'steer', 'handbrake', 'cameraReset', 'pause', 'vehicleReset', 'hud'] as const;
export type Action = typeof ACTIONS[number];
export type Actions = Record<Action, number>;
export type Bindings = Record<Action, Binding[]>;
const key = (code: string, scale = 1): Binding => ({ kind: 'key', code, scale });
const axis = (index: number, scale = 1): Binding => ({ kind: 'axis', index, scale });
const button = (index: number): Binding => ({ kind: 'button', index });
export const DEFAULT_BINDINGS: Bindings = {
  moveX: [axis(0), key('KeyD'), key('ArrowRight'), key('KeyA', -1), key('ArrowLeft', -1)],
  moveY: [axis(1, -1), key('KeyW'), key('ArrowUp'), key('KeyS', -1), key('ArrowDown', -1)],
  lookX: [axis(2)], lookY: [axis(3)],
  sprint: [button(0), key('ShiftLeft'), key('ShiftRight')], walk: [key('AltLeft'), key('AltRight')],
  jump: [button(2), key('Space')], interact: [button(3), key('KeyF')],
  throttle: [button(7), key('KeyW'), key('ArrowUp')], brake: [button(6), key('KeyS'), key('ArrowDown')],
  steer: [axis(0), key('KeyD'), key('ArrowRight'), key('KeyA', -1), key('ArrowLeft', -1)],
  handbrake: [button(5), key('Space')], cameraReset: [button(11), key('KeyC')],
  pause: [button(9), key('Escape')], vehicleReset: [key('KeyR')], hud: [key('KeyH')],
};
export const emptyActions = (): Actions => Object.fromEntries(ACTIONS.map(a => [a, 0])) as Actions;
interface Curve { deadZone: number; exponent: number }
export const MOVE_CURVE: Curve = { deadZone: 0.16, exponent: 1.35 };
const LOOK_CURVE: Curve = { deadZone: 0.12, exponent: 1.6 };
export const clamp = (x: number, lo = -1, hi = 1) => Math.min(hi, Math.max(lo, Number.isFinite(x) ? x : 0));
/** Circular dead zone, continuous at the edge, direction preserved even at diagonals. */
export function radial(x: number, y: number, curve: Curve = MOVE_CURVE): [number, number] {
  x = clamp(x); y = clamp(y);
  const r = Math.hypot(x, y), dz = clamp(curve.deadZone, 0, 0.95);
  if (r <= dz) return [0, 0];
  const m = ((Math.min(1, r) - dz) / (1 - dz)) ** Math.max(0.1, curve.exponent);
  return [x / r * m, y / r * m];
}
export function mapActions(pad: Gamepad | null, keys: ReadonlySet<string>, bindings: Bindings): Actions {
  const axes = pad?.mapping === 'standard' && pad.connected ? [
    ...radial(pad.axes[0] ?? 0, pad.axes[1] ?? 0),
    ...radial(pad.axes[2] ?? 0, pad.axes[3] ?? 0, LOOK_CURVE),
  ] : [];
  const out = emptyActions();
  for (const action of ACTIONS) {
    let keyboard = 0, gamepad = 0;
    for (const b of bindings[action]) {
      if (b.kind === 'key') keyboard += keys.has(b.code) ? b.scale ?? 1 : 0;
      else if (b.kind === 'axis') gamepad += (axes[b.index] ?? 0) * (b.scale ?? 1);
      else if (axes.length) gamepad += clamp(pad?.buttons[b.index]?.value ?? 0, 0, 1);
    }
    out[action] = clamp(Math.abs(keyboard) > Math.abs(gamepad) ? keyboard : gamepad);
  }
  const length = Math.hypot(out.moveX, out.moveY);
  if (length > 1) { out.moveX /= length; out.moveY /= length; }
  return out;
}
const XBOX_BUTTONS = ['Ⓐ', 'Ⓑ', 'ⓧ', 'Ⓨ', 'LB', 'RB', 'LT', 'RT', 'View', 'Menu', 'L3', 'R3', '↑', '↓', '←', '→', 'Xbox'];
export function glyph(action: Action, bindings: Bindings, gamepad: boolean): string {
  const b = bindings[action].find(b => gamepad ? b.kind !== 'key' : b.kind === 'key');
  if (!b) return '—';
  if (b.kind === 'button') return XBOX_BUTTONS[b.index] ?? `B${b.index}`;
  if (b.kind === 'axis') return b.index < 2 ? 'Ⓛ' : 'Ⓡ';
  return b.code.replace(/^Key|^Arrow/, '').replace(/Left$|Right$/, '');
}
