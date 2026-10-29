import { describe, expect, test, vi, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { ACTIONS, DEFAULT_BINDINGS, MOVE_CURVE, glyph, mapActions, radial, Input } from '../../src/input';
const bindings = () => structuredClone(DEFAULT_BINDINGS);
function pad(axes = [0, 0, 0, 0], buttons: Record<number, number> = {}): Gamepad {
  return { id: 'Xbox Wireless Controller', index: 0, connected: true, mapping: 'standard', timestamp: 0, axes,
    buttons: Array.from({ length: 17 }, (_, i) => ({ value: buttons[i] ?? 0, pressed: (buttons[i] ?? 0) > 0.5, touched: false })),
  } as unknown as Gamepad;
}
afterEach(() => vi.unstubAllGlobals());
describe('action mapping', () => {
  test('radial dead zone is continuous, symmetric and preserves diagonal direction', () => {
    expect(radial(0.1, 0.1)).toEqual([0, 0]);
    expect(radial(MOVE_CURVE.deadZone, 0)).toEqual([0, 0]);
    expect(radial(MOVE_CURVE.deadZone + 0.00001, 0)[0]).toBeLessThan(0.00001);
    expect(radial(1, 0)).toEqual([1, 0]);
    const [x, y] = radial(1, -1);
    expect(Math.hypot(x, y)).toBeCloseTo(1); expect(x).toBeCloseTo(-y);
    expect(radial(0.5, 0)[0]).toBeGreaterThan(0); expect(radial(0.5, 0)[0]).toBeLessThan(0.5);
    expect(radial(NaN, Infinity)).toEqual([0, 0]);
  });
  test('fake Xbox covers every standard action and analog triggers', () => {
    const a = mapActions(pad([1, -1, 0.8, -0.7], { 0: 1, 2: 1, 3: 1, 5: 1, 6: 0.25, 7: 0.8, 8: 1, 9: 1, 11: 1, 13: 1 }), new Set(), bindings());
    expect(a.moveX).toBeCloseTo(Math.SQRT1_2); expect(a.moveY).toBeCloseTo(Math.SQRT1_2);
    expect(a.lookX).toBeGreaterThan(0); expect(a.lookY).toBeLessThan(0);
    for (const action of ['sprint', 'jump', 'interact', 'mission', 'handbrake', 'cameraReset', 'pause', 'mute', 'map'] as const) expect(a[action]).toBe(1);
    expect(glyph('mission', bindings(), true)).toBe('Ⓐ'); expect(glyph('mission', bindings(), false)).toBe('E');
    expect(a.throttle).toBe(0.8); expect(a.brake).toBe(0.25); expect(a.steer).toBe(a.moveX);
  });
  test('B flips the car without activating another driving control', () => {
    const a = mapActions(pad([0, 0, 0, 0], { 1: 1 }), new Set(), bindings());
    expect(a.vehicleReset).toBe(1);
    for (const action of ['interact', 'mission', 'throttle', 'brake', 'steer', 'handbrake', 'cameraReset', 'pause', 'map', 'mute'] as const) expect(a[action]).toBe(0);
    expect(glyph('vehicleReset', bindings(), true)).toBe('Ⓑ');
  });
  test('keyboard shares actions, normalizes diagonals, cancels opposites and ignores nonstandard pads', () => {
    const a = mapActions({ ...pad(), mapping: '' } as Gamepad, new Set(['KeyW', 'KeyD', 'ShiftLeft', 'Space', 'KeyF']), bindings());
    expect(a.moveX).toBeCloseTo(Math.SQRT1_2); expect(a.moveY).toBeCloseTo(Math.SQRT1_2);
    expect(a.sprint).toBe(1); expect(a.jump).toBe(1); expect(a.interact).toBe(1);
    expect(mapActions(null, new Set(['KeyA', 'KeyD']), bindings()).moveX).toBe(0);
  });
  test('rebinding survives JSON serialization and glyphs track the binding', () => {
    const b = bindings(); b.jump = [{ kind: 'button', index: 1 }, { kind: 'key', code: 'KeyJ' }];
    const restored = JSON.parse(JSON.stringify(b));
    expect(mapActions(pad([], { 1: 1 }), new Set(), restored).jump).toBe(1);
    expect(mapActions(null, new Set(['Space']), restored).jump).toBe(0);
    expect(glyph('jump', restored, true)).toBe('Ⓑ'); expect(glyph('jump', restored, false)).toBe('J');
    expect(ACTIONS.every(a => a in restored)).toBe(true);
  });
});

test('hot plug, edge actions, tap/hold sprint, focus clearing and unsupported rumble', async () => {
  const events = new EventTarget(), el = new EventTarget();
  let pads: (Gamepad | null)[] = [];
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => pads });
  const input = new Input(el as HTMLElement);
  input.update(1 / 60); expect(input.actions.moveX).toBe(0);
  pads = [null, pad([0, -1, 0, 0], { 0: 1, 2: 1 })];
  input.update(1 / 60); expect(input.source).toBe('gamepad'); expect(input.consume('jump')).toBe(true);
  expect(input.consume('jump')).toBe(false); expect(input.sprinting).toBe(true);
  input.update(1 / 60); expect(input.consume('jump')).toBe(false);
  pads = [pad([0, -1, 0, 0])]; input.update(0.1); expect(input.sprinting).toBe(true);
  input.update(0.5); expect(input.sprinting).toBe(false);
  expect(await input.rumble()).toBe(false);
  const playEffect = vi.fn().mockResolvedValue('complete');
  pads = [{ ...pad(), vibrationActuator: { playEffect } } as unknown as Gamepad];
  input.update(0.01); expect(await input.rumble(90, 0.2, 0.1)).toBe(true);
  expect(playEffect).toHaveBeenCalledWith('dual-rumble', expect.objectContaining({ duration: 90, strongMagnitude: 0.2 }));
  playEffect.mockRejectedValue(new Error('unsupported')); expect(await input.rumble()).toBe(false);
  pads = []; input.update(0.01); expect(input.source).toBe('keyboard'); expect(input.actions.moveY).toBe(0);
  const key = (type: string) => { const e = new Event(type); Object.assign(e, { code: 'KeyF', repeat: false }); events.dispatchEvent(e); };
  key('keydown'); key('keyup'); input.update(0.01); expect(input.consume('interact')).toBe(true);
  key('keydown'); input.update(0.01); expect(input.consume('interact')).toBe(true);
  key('keyup'); key('keydown'); key('keyup'); input.update(0.01);
  expect(input.consume('interact'), 'a tap after a held frame must remain an edge').toBe(true);
  key('keydown'); events.dispatchEvent(new Event('blur')); input.update(0.01); expect(input.consume('interact')).toBe(false);
  input.dispose();
});

test('gameplay cannot read browser input outside src/input', () => {
  const root = path.resolve(__dirname, '../../src');
  const offenders = fs.readdirSync(root, { recursive: true }).map(String).filter(f => f.endsWith('.ts') && !f.startsWith('input/'))
    .filter(f => /getGamepads|KeyboardEvent|MouseEvent|GamepadEvent|addEventListener\(['"](?:keydown|keyup|mousemove|pointermove|gamepadconnected|gamepaddisconnected)/.test(fs.readFileSync(path.join(root, f), 'utf8')));
  expect(offenders).toEqual([]);
});

test('keyboard autorepeat does not activate audio or recreate input edges', () => {
  const events = new EventTarget(), activate = vi.fn();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => [] });
  const input = new Input(new EventTarget() as HTMLElement, activate);
  const down = (repeat: boolean) => {
    const e = new Event('keydown'); Object.assign(e, { code: 'KeyF', repeat }); events.dispatchEvent(e);
  };
  down(false); input.update(1 / 60); expect(input.consume('interact')).toBe(true);
  for (let i = 0; i < 30; i++) down(true);
  input.update(1 / 60);
  expect(input.consume('interact')).toBe(false);
  expect(activate).toHaveBeenCalledTimes(1);
  input.dispose();
});


test('F10 requests fullscreen within the gesture and ignores autorepeat', () => {
  const events = new EventTarget(), toggle = vi.fn();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => [] });
  const input = new Input(new EventTarget() as HTMLElement, () => {}, toggle);
  const key = (repeat: boolean) => events.dispatchEvent(Object.assign(new Event('keydown', { cancelable: true }), { code: 'F10', repeat }));
  expect(key(false)).toBe(false);
  expect(toggle).toHaveBeenCalledTimes(1); // no frame update or host gamepad read
  key(true);
  expect(toggle).toHaveBeenCalledTimes(1);
  input.dispose();
});


test('photo shortcuts use free controls and clipboard runs in the original key gesture', () => {
  const a = mapActions(pad([0, 0, 0, 0], { 14: 1, 15: 1 }), new Set(), bindings());
  expect(Object.entries(a).filter(([, value]) => value !== 0).map(([action]) => action).sort())
    .toEqual(['photoCharacter', 'positionHud']);
  const keyboard = mapActions(null, new Set(['KeyP', 'KeyV', 'KeyL']), bindings());
  expect(keyboard.positionHud).toBe(1); expect(keyboard.photoCharacter).toBe(1); expect(keyboard.copyPosition).toBe(1);
  const events = new EventTarget(), el = new EventTarget();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', {});
  const copy = vi.fn();
  const input = new Input(el as HTMLElement, undefined, undefined, copy);
  events.dispatchEvent(Object.assign(new Event('keydown'), { code: 'KeyL', repeat: false }));
  expect(copy).toHaveBeenCalledTimes(1);
  events.dispatchEvent(Object.assign(new Event('keydown'), { code: 'KeyL', repeat: true }));
  expect(copy).toHaveBeenCalledTimes(1);
  input.dispose();
});
