import { describe, expect, test, vi, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { ACTIONS, DEFAULT_BINDINGS, MOVE_CURVE, emptyActions, mapActions, promptLabel, radial, Input } from '../../src/input';
import { playRumble, rumbleDriver, stopRumble } from '../../src/input/actuator';
const bindings = () => structuredClone(DEFAULT_BINDINGS);
function pad(axes = [0, 0, 0, 0], buttons: Record<number, number> = {}): Gamepad {
  return { id: 'Xbox Wireless Controller', index: 0, connected: true, mapping: 'standard', timestamp: 0, axes,
    buttons: Array.from({ length: 17 }, (_, i) => ({ value: buttons[i] ?? 0, pressed: (buttons[i] ?? 0) > 0.5, touched: false })),
  } as unknown as Gamepad;
}
afterEach(() => vi.unstubAllGlobals());
describe('action mapping', () => {
  test('zero snapshots include every action and remain independent mutable inputs', () => {
    const first = emptyActions(), second = emptyActions();
    expect(Object.keys(first)).toEqual([...ACTIONS]);
    expect(Object.values(first).every(value => value === 0)).toBe(true);
    first.throttle = 1; first.jump = 1;
    expect(second.throttle).toBe(0); expect(second.jump).toBe(0);
    expect(emptyActions()).toEqual(second);
  });

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
    expect(promptLabel('mission', bindings(), 'xbox')).toBe('Ⓐ'); expect(promptLabel('mission', bindings(), 'keyboard')).toBe('E');
    expect(a.throttle).toBe(0.8); expect(a.brake).toBe(0.25); expect(a.steer).toBe(a.moveX);
  });
  test('B flips the car without activating another driving control', () => {
    const a = mapActions(pad([0, 0, 0, 0], { 1: 1 }), new Set(), bindings());
    expect(a.vehicleReset).toBe(1);
    for (const action of ['interact', 'mission', 'throttle', 'brake', 'steer', 'handbrake', 'cameraReset', 'pause', 'map', 'mute'] as const) expect(a[action]).toBe(0);
    expect(promptLabel('vehicleReset', bindings(), 'xbox')).toBe('Ⓑ');
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
    expect(promptLabel('jump', restored, 'xbox')).toBe('Ⓑ'); expect(promptLabel('jump', restored, 'keyboard')).toBe('J');
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


test('steer from held keys is marked for the car to slew; a driver steering through the actions is not', () => {
  const events = new EventTarget();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => [] });
  const input = new Input(new EventTarget() as HTMLElement, () => {});
  const key = (type: 'keydown' | 'keyup', code: string) => events.dispatchEvent(Object.assign(new Event(type), { code, repeat: false }));
  key('keydown', 'KeyA'); input.update(1 / 60);
  expect(input.actions.steer).toBe(-1); expect(input.steerFromKeys).toBe(true);
  // An autopilot or a probe writing analog steer through the driver hook.
  input.driver = actions => { actions.steer = 0.3; };
  input.update(1 / 60);
  expect(input.steerFromKeys).toBe(false);
  input.driver = null; key('keyup', 'KeyA'); input.update(1 / 60);
  // Released keys centre at the keyboard rate too.
  expect(input.actions.steer).toBe(0); expect(input.steerFromKeys).toBe(true);
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
  const keyboard = mapActions(null, new Set(['KeyP', 'KeyV', 'KeyJ']), bindings());
  expect(keyboard.positionHud).toBe(1); expect(keyboard.photoCharacter).toBe(1); expect(keyboard.copyPosition).toBe(1);
  const events = new EventTarget(), el = new EventTarget();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', {});
  const copy = vi.fn();
  const input = new Input(el as HTMLElement, undefined, undefined, copy);
  events.dispatchEvent(Object.assign(new Event('keydown'), { code: 'KeyJ', repeat: false }));
  expect(copy).toHaveBeenCalledTimes(1);
  events.dispatchEvent(Object.assign(new Event('keydown'), { code: 'KeyJ', repeat: true }));
  expect(copy).toHaveBeenCalledTimes(1);
  input.dispose();
});

test('developer time keys use free codes and pass a focused time panel control; other keys stay with the control', () => {
  const time = ['timePanel', 'timePause', 'timeSpeed1', 'timeSpeed2', 'timeSpeed3', 'timeSpeed4', 'timeHourBack', 'timeHourForward',
    'timeMinutesBack', 'timeMinutesForward', 'timeLive'] as const;
  const codes = (actions: readonly string[]) => actions.flatMap(a => DEFAULT_BINDINGS[a as keyof typeof DEFAULT_BINDINGS])
    .flatMap(b => b.kind === 'key' ? [b.code] : []);
  const others = new Set(codes(ACTIONS.filter(a => !(time as readonly string[]).includes(a))));
  expect(codes(time).filter(code => others.has(code))).toEqual([]);
  expect(new Set(codes(time)).size).toBe(time.length);
  const events = new EventTarget();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => [] });
  const input = new Input(new EventTarget() as HTMLElement);
  const inPanel = { closest: (selector: string) => selector.includes('#time-panel') ? {} : null };
  const key = (code: string, target: unknown) => {
    const e = Object.assign(new Event('keydown', { cancelable: true }), { code, repeat: false });
    Object.defineProperty(e, 'target', { value: target });
    events.dispatchEvent(e);
    events.dispatchEvent(Object.assign(new Event('keyup'), { code }));
  };
  key('ArrowRight', inPanel); key('KeyW', inPanel); key('BracketRight', inPanel); key('Digit4', inPanel);
  input.update(1 / 60);
  expect(input.consume('timeHourForward')).toBe(true); expect(input.consume('timeSpeed4')).toBe(true);
  expect(input.consume('menuDown')).toBe(false); expect(input.consume('menuUp')).toBe(false);
  key('KeyW', null);
  input.update(1 / 60);
  expect(input.consume('menuUp')).toBe(true);
  // A slider drag that starts in the panel never turns the camera, even where the pointer leaves the panel.
  vi.stubGlobal('document', { pointerLockElement: null });
  const pointer = (type: string, target: unknown, fields: object = {}) => {
    const e = Object.assign(new Event(type), { pointerType: 'mouse', buttons: 1, movementX: 40, movementY: 0, ...fields });
    Object.defineProperty(e, 'target', { value: target });
    events.dispatchEvent(e);
  };
  pointer('pointerdown', inPanel); pointer('mousemove', inPanel); pointer('mousemove', null);
  input.update(1 / 60);
  expect(input.lookX).toBe(0);
  pointer('pointerup', null, { buttons: 0 }); pointer('pointerdown', null); pointer('mousemove', null);
  input.update(1 / 60);
  expect(input.lookX).toBeGreaterThan(0);
  input.dispose();
});

describe('rumble on any pad', () => {
  const sony = 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)';
  const rig = () => {
    let pads: (Gamepad | null)[] = [];
    const events = new EventTarget();
    vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
    vi.stubGlobal('navigator', { getGamepads: () => pads });
    const input = new Input(new EventTarget() as HTMLElement);
    return { input, events, set: (...list: (Gamepad | null)[]) => { pads = list; input.update(1 / 60); } };
  };
  const withPad = (extra: object, index = 0, id = 'Xbox Wireless Controller', buttons: Record<number, number> = {}) =>
    ({ ...pad([0, 0, 0, 0], buttons), id, index, ...extra }) as unknown as Gamepad;
  const effect = (extra: object = {}) => {
    const playEffect = vi.fn().mockResolvedValue('complete'), reset = vi.fn().mockResolvedValue('complete');
    return { playEffect, reset, actuator: { playEffect, reset, ...extra } };
  };

  test('detection: dual-rumble by capability, hapticActuators pulse as the fallback, nothing without an actuator', async () => {
    const chrome = effect({ effects: ['dual-rumble', 'trigger-rumble'] }), old = effect({ type: 'dual-rumble' });
    const safari = effect({ canPlayEffectType: (t: string) => t === 'dual-rumble' }), bare = effect();
    for (const a of [chrome, old, safari, bare]) {
      expect(rumbleDriver({ vibrationActuator: a.actuator })).toBe('dual-rumble');
      expect(await playRumble({ vibrationActuator: a.actuator }, 2000, 0.4, 2)).toBe(true);
      expect(a.playEffect).toHaveBeenCalledWith('dual-rumble', { duration: 1000, startDelay: 0, strongMagnitude: 0.4, weakMagnitude: 1 });
    }
    chrome.playEffect.mockResolvedValue('preempted');
    expect(await playRumble({ vibrationActuator: chrome.actuator }, 100, 0.4, 0.2)).toBe(true);
    // An actuator that cannot play dual-rumble is not asked; with pulse actuators the pad still vibrates.
    const triggers = effect({ effects: ['trigger-rumble'] }), vibration = effect({ type: 'vibration' });
    const none = effect({ canPlayEffectType: () => false });
    for (const a of [triggers, vibration, none]) {
      expect(rumbleDriver({ vibrationActuator: a.actuator })).toBe('none');
      expect(await playRumble({ vibrationActuator: a.actuator }, 100, 0.4, 0.2)).toBe(false);
      expect(a.playEffect).not.toHaveBeenCalled();
    }
    const one = vi.fn().mockResolvedValue(true), strong = vi.fn().mockResolvedValue(true), weak = vi.fn().mockResolvedValue(true);
    expect(rumbleDriver({ vibrationActuator: triggers.actuator, hapticActuators: [{ pulse: one }] })).toBe('pulse');
    expect(await playRumble({ hapticActuators: [{ pulse: one }] }, 120, 0.3, 0.7)).toBe(true);
    expect(one).toHaveBeenCalledWith(0.7, 120);
    expect(await playRumble({ hapticActuators: [{ pulse: strong }, { pulse: weak }] }, 120, 0.3, 0.7)).toBe(true);
    expect(strong).toHaveBeenCalledWith(0.3, 120); expect(weak).toHaveBeenCalledWith(0.7, 120);
    await stopRumble({ hapticActuators: [{ pulse: strong }, { pulse: weak }] });
    expect(strong).toHaveBeenLastCalledWith(0, 1); expect(weak).toHaveBeenLastCalledWith(0, 1);
    for (const p of [null, {}, { vibrationActuator: null, hapticActuators: [] }, { hapticActuators: [null, {}] }]) {
      expect(rumbleDriver(p)).toBe('none');
      expect(await playRumble(p, 100, 1, 1)).toBe(false);
      await stopRumble(p);
    }
    // Stop: reset where the actuator has it, else a zero effect.
    await stopRumble({ vibrationActuator: chrome.actuator }); expect(chrome.reset).toHaveBeenCalledOnce();
    const noReset = { playEffect: vi.fn().mockResolvedValue('complete') };
    await stopRumble({ vibrationActuator: noReset });
    expect(noReset.playEffect).toHaveBeenCalledWith('dual-rumble', expect.objectContaining({ strongMagnitude: 0, weakMagnitude: 0 }));
  });

  test('the pad in use rumbles: a Sony pad like an Xbox pad, the latest input wins, hot plug and index changes follow', async () => {
    const { input, events, set } = rig();
    const xbox = effect({ effects: ['dual-rumble'] }), dualsense = effect({ effects: ['dual-rumble'] });
    expect(input.rumbleDriver).toBe('none');
    set(withPad({ vibrationActuator: xbox.actuator }), withPad({ vibrationActuator: dualsense.actuator }, 1, sony));
    expect(input.rumbleDriver).toBe('dual-rumble');
    expect(await input.rumble(250, 0.55, 1)).toBe(true);
    expect(xbox.playEffect).toHaveBeenCalledOnce(); expect(dualsense.playEffect).not.toHaveBeenCalled();
    // The DualSense gets input: it takes over with the same magnitudes, and the Xbox pad left behind stops.
    set(withPad({ vibrationActuator: xbox.actuator }), withPad({ vibrationActuator: dualsense.actuator }, 1, sony, { 0: 1 }));
    expect(input.padFamily).toBe('playstation'); expect(xbox.reset).toHaveBeenCalledOnce();
    expect(await input.rumble(250, 0.55, 1)).toBe(true);
    expect(dualsense.playEffect).toHaveBeenCalledWith('dual-rumble', { duration: 250, startDelay: 0, strongMagnitude: 0.55, weakMagnitude: 1 });
    expect(xbox.playEffect).toHaveBeenCalledOnce();
    // It stays the pad in use without further input; zero magnitudes stop it.
    set(withPad({ vibrationActuator: xbox.actuator }), withPad({ vibrationActuator: dualsense.actuator }, 1, sony));
    expect(await input.rumble(0, 0, 0)).toBe(false); expect(dualsense.reset).toHaveBeenCalledOnce();
    // Unplugged mid-drive: nothing to drive. Back under another index: it rumbles again.
    set(); expect(input.rumbleDriver).toBe('none'); expect(await input.rumble(250, 0.55, 1)).toBe(false);
    set(null, null, withPad({ vibrationActuator: dualsense.actuator }, 2, sony));
    expect(await input.rumble(250, 0.3, 0.6)).toBe(true); expect(dualsense.playEffect).toHaveBeenCalledTimes(2);
    // A pad without any actuator is silent, a pulse-only pad is pulsed.
    set(withPad({}, 0, 'Generic USB Joystick'));
    expect(input.rumbleDriver).toBe('none'); expect(await input.rumble(250, 0.3, 0.6)).toBe(false);
    const pulse = vi.fn().mockResolvedValue(true);
    set(withPad({ hapticActuators: [{ pulse }] }, 0, 'Generic USB Joystick'));
    expect(input.rumbleDriver).toBe('pulse'); expect(await input.rumble(250, 0.3, 0.6)).toBe(true);
    expect(pulse).toHaveBeenCalledWith(0.6, 250);
    // Losing focus stops the pad and nothing plays until the window has focus again.
    events.dispatchEvent(new Event('blur')); expect(pulse).toHaveBeenLastCalledWith(0, 1);
    input.update(1 / 60); expect(await input.rumble(250, 0.3, 0.6)).toBe(false);
    events.dispatchEvent(new Event('focus')); input.update(1 / 60); expect(await input.rumble(250, 0.3, 0.6)).toBe(true);
  });
});
