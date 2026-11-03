/** Serializable per-action bindings. Standard Gamepad layout, independent of device name. */
export type Binding =
  | { kind: 'axis'; index: number; scale?: number }
  | { kind: 'button'; index: number }
  | { kind: 'key'; code: string; scale?: number }
  /** Every listed Standard Gamepad button held at once (the editor toggle's Xbox fallback, L3 + R3). */
  | { kind: 'chord'; indices: number[] };
export const ACTIONS = ['moveX', 'moveY', 'lookX', 'lookY', 'sprint', 'walk', 'jump', 'interact', 'mission',
  'throttle', 'brake', 'steer', 'handbrake', 'horn', 'lights', 'cameraReset', 'pause', 'mute', 'nextTrack', 'vehicleReset', 'respawn', 'hud', 'positionHud', 'layerPanel', 'photoCharacter', 'copyPosition', 'map',
  'menuUp', 'menuDown', 'menuSelect', 'menuBack', 'flyUp', 'flyDown',
  'timePanel', 'timePause', 'timeSpeed1', 'timeSpeed2', 'timeSpeed3', 'timeSpeed4', 'timeHourBack', 'timeHourForward',
  'timeMinutesBack', 'timeMinutesForward', 'timeLive',
  // Developer object editor (dev/editor): only editToggle is bound outside the editor layer (EDITOR_BINDINGS).
  'editToggle', 'editPick', 'editBack', 'editPrev', 'editNext', 'editDelete', 'editGround', 'editTurnLeft', 'editTurnRight',
  'editUp', 'editDown', 'editLeft', 'editRight', 'editLidar'] as const;
export type Action = typeof ACTIONS[number];
export type Actions = Record<Action, number>;
export type Bindings = Record<Action, Binding[]>;
const key = (code: string, scale = 1): Binding => ({ kind: 'key', code, scale });
const axis = (index: number, scale = 1): Binding => ({ kind: 'axis', index, scale });
const button = (index: number): Binding => ({ kind: 'button', index });
const chord = (...indices: number[]): Binding => ({ kind: 'chord', indices });
export const DEFAULT_BINDINGS: Bindings = {
  moveX: [axis(0), key('KeyD'), key('ArrowRight'), key('KeyA', -1), key('ArrowLeft', -1)],
  moveY: [axis(1, -1), key('KeyW'), key('ArrowUp'), key('KeyS', -1), key('ArrowDown', -1)],
  lookX: [axis(2)], lookY: [axis(3)],
  sprint: [button(0), key('ShiftLeft'), key('ShiftRight')], walk: [key('AltLeft'), key('AltRight')],
  jump: [button(2), key('Space')], interact: [button(3), key('KeyF')],
  // Mission targets (gameplay/missions): A shares the sprint button, which only acts while moving.
  mission: [button(0), key('KeyE')],
  throttle: [button(7), key('KeyW'), key('ArrowUp')], brake: [button(6), key('KeyS'), key('ArrowDown')],
  steer: [axis(0), key('KeyD'), key('ArrowRight'), key('KeyA', -1), key('ArrowLeft', -1)],
  horn: [button(10), key('KeyG')], lights: [button(12), key('KeyL')],
  handbrake: [button(5), key('Space')], cameraReset: [button(11), key('KeyC')],
  pause: [button(9), key('Escape')], mute: [button(13), key('KeyK')], nextTrack: [key('KeyN')],
  vehicleReset: [button(1), key('KeyR')], hud: [key('F3'), key('KeyH')],
  // Safe reset (gameplay/recovery.ts): read only while the player is lost, when Y does not enter or exit a car.
  respawn: [button(3), key('KeyR')],
  positionHud: [button(14), key('KeyP')], photoCharacter: [button(15), key('KeyV')],
  layerPanel: [button(4), key('F4')],
  copyPosition: [key('KeyJ')],
  map: [button(8), key('KeyM')],
  // Shop menus (ui/shopMenu.ts): D-pad or left stick, A selects, B leaves. Read only while a menu is open.
  menuUp: [button(12), axis(1, -1), key('ArrowUp'), key('KeyW')],
  menuDown: [button(13), axis(1), key('ArrowDown'), key('KeyS')],
  menuSelect: [button(0), key('Enter'), key('NumpadEnter'), key('KeyE')],
  menuBack: [button(1), key('Escape'), key('Backspace')],
  // Developer fly mode (gameplay/flyMode.ts, localhost only): rise with jump or RT, sink with B, LT or Q.
  flyUp: [button(2), button(7), key('Space')],
  flyDown: [button(1), button(6), key('KeyQ')],
  // Developer time panel (ui/timePanel.ts, developer tools only): T shows it; the rest act while it shows. Brackets are
  // the two keys right of P (Å and ¨ on a Finnish keyboard). The gamepad uses Developer → Time of day in the pause menu.
  timePanel: [key('KeyT')], timePause: [key('Digit0')],
  timeSpeed1: [key('Digit1')], timeSpeed2: [key('Digit2')], timeSpeed3: [key('Digit3')], timeSpeed4: [key('Digit4')],
  timeHourBack: [key('BracketLeft')], timeHourForward: [key('BracketRight')],
  timeMinutesBack: [key('Comma')], timeMinutesForward: [key('Period')], timeLive: [key('KeyB')],
  // Developer object editor (dev/editor/README.md): the DualSense touchpad click (17), L3 + R3 on an Xbox pad, or F8.
  editToggle: [button(17), chord(10, 11), key('F8')],
  editPick: [], editBack: [], editPrev: [], editNext: [], editDelete: [], editGround: [], editTurnLeft: [], editTurnRight: [],
  editUp: [], editDown: [], editLeft: [], editRight: [], editLidar: [],
};
/** Editor-layer bindings (Standard Gamepad: Cross/A 0, Circle/B 1, Square/X 2, Triangle/Y 3, L1 4, R1 5, L2 6, R2 7,
 *  Create/View 8, L3 10, D-pad 12-15). Square and the triggers stay shared with jump and fly up/down: the editor reads
 *  editGround / editTurn* only while it holds an object, and then zeroes the flight actions itself. */
const EDITOR_ONLY: Partial<Bindings> = {
  editPick: [button(0), key('Enter'), key('NumpadEnter')], editBack: [button(1), key('Backspace')],
  editPrev: [button(4), key('KeyZ')], editNext: [button(5), key('KeyX')], editDelete: [button(3), key('Delete')],
  editGround: [button(2), key('KeyO')], editTurnLeft: [button(6), key('Comma')], editTurnRight: [button(7), key('Period')],
  editUp: [button(12), key('PageUp')], editDown: [button(13), key('PageDown')], editLeft: [button(14), key('Home')],
  editRight: [button(15), key('End')], editLidar: [button(8), key('KeyL')],
};
/** Buttons and keys the editor layer takes from gameplay: everything but the sticks, Square, the triggers, Options
 *  and R3 (camera reset), so walking, flying, looking and pausing work unchanged in the editor. */
const EDITOR_BUTTONS = new Set([0, 1, 3, 4, 5, 8, 10, 12, 13, 14, 15, 17]);
const EDITOR_KEYS = new Set(Object.values(EDITOR_ONLY).flatMap(list => list!.flatMap(b => b.kind === 'key' ? [b.code] : [])));
const EDITOR_ACTIONS = new Set<string>(['editToggle', ...Object.keys(EDITOR_ONLY)]);
/** The editor layer over `game` bindings: editor actions bound, their buttons and keys removed from gameplay actions,
 *  and L3 as the fast-flight modifier (sprint) in place of Cross. */
export function editorBindings(game: Bindings): Bindings {
  const out = structuredClone(game);
  for (const action of ACTIONS) {
    if (EDITOR_ACTIONS.has(action)) continue;
    out[action] = out[action].filter(b => b.kind === 'button' ? !EDITOR_BUTTONS.has(b.index)
      : b.kind === 'key' ? !EDITOR_KEYS.has(b.code) : true);
  }
  for (const [action, list] of Object.entries(EDITOR_ONLY)) out[action as Action] = structuredClone(list!);
  out.sprint = [button(10), ...out.sprint];
  return out;
}
const ZERO_ACTIONS = Object.freeze(Object.fromEntries(ACTIONS.map(a => [a, 0]))) as Readonly<Actions>;
/** Fresh mutable snapshot, without rebuilding per-action entry arrays in every input sample. */
export const emptyActions = (): Actions => ({ ...ZERO_ACTIONS });
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
      else if (b.kind === 'chord') { if (axes.length) gamepad += Math.min(...b.indices.map(i => clamp(pad?.buttons[i]?.value ?? 0, 0, 1))); }
      else if (axes.length) gamepad += clamp(pad?.buttons[b.index]?.value ?? 0, 0, 1);
    }
    out[action] = clamp(Math.abs(keyboard) > Math.abs(gamepad) ? keyboard : gamepad);
  }
  const length = Math.hypot(out.moveX, out.moveY);
  if (length > 1) { out.moveX /= length; out.moveY /= length; }
  return out;
}
const XBOX_BUTTONS = ['Ⓐ', 'Ⓑ', 'ⓧ', 'Ⓨ', 'LB', 'RB', 'LT', 'RT', 'View', 'Menu', 'L3', 'R3', '↑', '↓', '←', '→', 'Xbox'];
/** PlayStation names of the Standard Gamepad buttons (DualSense, DualShock 4); 17 is the touchpad click. */
const PLAYSTATION_BUTTONS = ['✕', '○', '□', '△', 'L1', 'R1', 'L2', 'R2', 'Create', 'Options', 'L3', 'R3', '↑', '↓', '←', '→', 'PS', 'Touchpad'];
/** A pad id that reports a Sony controller (Chrome: "DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c …)"). */
export const playstationPad = (id: string | null | undefined) => !!id && /dualsense|dualshock|wireless controller.*054c|vendor: 054c|playstation/i.test(id);
export function glyph(action: Action, bindings: Bindings, gamepad: boolean, playstation = false): string {
  const b = bindings[action].find(b => gamepad ? b.kind !== 'key' : b.kind === 'key');
  if (!b) return '—';
  const names = playstation ? PLAYSTATION_BUTTONS : XBOX_BUTTONS;
  if (b.kind === 'chord') return b.indices.map(i => names[i] ?? `B${i}`).join('+');
  if (b.kind === 'button') return names[b.index] ?? `B${b.index}`;
  if (b.kind === 'axis') return b.index < 2 ? 'Ⓛ' : 'Ⓡ';
  return b.code.replace(/^Key|^Arrow/, '').replace(/Left$|Right$/, '');
}
