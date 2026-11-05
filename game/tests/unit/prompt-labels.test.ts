import { afterEach, describe, expect, test, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { DEFAULT_BINDINGS, Input, keyName, padFamily, promptLabel, type Action, type Bindings } from '../../src/input';
import { DIALOGUE } from '../../src/gameplay/content/dialogue';

const DUALSENSE = 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)';
const DUALSHOCK4 = 'Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 09cc)';
const XBOX = 'Xbox Wireless Controller (STANDARD GAMEPAD Vendor: 045e Product: 0b13)';
const label = (action: Action, device: Parameters<typeof promptLabel>[2], bindings: Bindings = DEFAULT_BINDINGS) =>
  promptLabel(action, bindings, device);

afterEach(() => vi.unstubAllGlobals());

describe('promptLabel', () => {
  test('PlayStation names: shapes, shoulders, sticks, Create/Options, the touchpad, the PS button and the D-pad', () => {
    const table: [Action, string][] = [['sprint', '✕'], ['vehicleReset', '○'], ['jump', '□'], ['interact', '△'],
      ['layerPanel', 'L1'], ['handbrake', 'R1'], ['brake', 'L2'], ['throttle', 'R2'], ['map', 'Create'], ['pause', 'Options'],
      ['horn', 'L3'], ['cameraReset', 'R3'], ['lights', '↑'], ['mute', '↓'], ['positionHud', '←'], ['photoCharacter', '→'],
      ['editToggle', 'Touchpad'], ['moveY', 'L stick'], ['steer', 'L stick'], ['lookX', 'R stick'], ['mission', '✕'], ['menuBack', '○']];
    for (const [action, name] of table) expect(label(action, 'playstation'), action).toBe(name);
    const ps: Bindings = { ...DEFAULT_BINDINGS, map: [{ kind: 'button', index: 16 }] };
    expect(label('map', 'playstation', ps)).toBe('PS');
  });

  test('Xbox names: A B X Y, LB RB LT RT, View, Menu; the missing touchpad yields to L3+R3', () => {
    const table: [Action, string][] = [['sprint', 'Ⓐ'], ['vehicleReset', 'Ⓑ'], ['jump', 'ⓧ'], ['interact', 'Ⓨ'],
      ['layerPanel', 'LB'], ['handbrake', 'RB'], ['brake', 'LT'], ['throttle', 'RT'], ['map', 'View'], ['pause', 'Menu'],
      ['horn', 'L3'], ['cameraReset', 'R3'], ['lights', '↑'], ['editToggle', 'L3+R3'], ['moveY', 'Ⓛ'], ['lookX', 'Ⓡ']];
    for (const [action, name] of table) expect(label(action, 'xbox'), action).toBe(name);
    expect(label('sprint', 'touch'), 'the touch overlay carries the Xbox glyphs').toBe('Ⓐ');
  });

  test('keyboard names: WASD, axis pairs, the mouse, modifiers and named keys', () => {
    const table: [Action, string][] = [['moveY', 'WASD'], ['moveX', 'WASD'], ['steer', 'A/D'], ['lookX', 'mouse'],
      ['sprint', 'Shift'], ['walk', 'Alt'], ['jump', 'Space'], ['mission', 'E'], ['interact', 'F'], ['pause', 'Esc'],
      ['menuUp', '↑'], ['menuSelect', 'Enter'], ['layerPanel', 'F4'], ['timeSpeed1', '1'], ['timeHourBack', '['], ['editToggle', 'Y']];
    for (const [action, name] of table) expect(label(action, 'keyboard'), action).toBe(name);
    expect(keyName('ControlLeft')).toBe('Ctrl'); expect(keyName('PageDown')).toBe('PgDn');
  });

  test('a pad without a binding for the action names its key; nothing bound reads as a dash', () => {
    expect(label('copyPosition', 'playstation')).toBe('J');
    expect(label('nextTrack', 'xbox')).toBe('N');
    expect(label('editPick', 'playstation'), 'editor actions are unbound outside the editor layer').toBe('—');
  });

  test('Sony pads get PlayStation names, every other pad Xbox names', () => {
    expect(padFamily(DUALSENSE)).toBe('playstation');
    expect(padFamily(DUALSHOCK4)).toBe('playstation');
    expect(padFamily('Sony PLAYSTATION(R)3 Controller')).toBe('playstation');
    expect(padFamily(XBOX)).toBe('xbox');
    expect(padFamily('8BitDo Pro 2 (STANDARD GAMEPAD Vendor: 2dc8 Product: 6006)')).toBe('xbox');
    expect(padFamily(null)).toBe('xbox');
  });
});

function pad(id: string, buttons: Record<number, number> = {}): Gamepad {
  return { id, index: 0, connected: true, mapping: 'standard', timestamp: 0, axes: [0, 0, 0, 0],
    buttons: Array.from({ length: 18 }, (_, i) => ({ value: buttons[i] ?? 0, pressed: (buttons[i] ?? 0) > 0.5, touched: false })),
  } as unknown as Gamepad;
}

test('Input prompts follow the device used last and redraw on every switch', () => {
  const events = new EventTarget();
  let pads: Gamepad[] = [];
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => pads });
  const input = new Input(new EventTarget() as HTMLElement);
  const seen: string[] = [];
  input.watchPrompts(() => seen.push(`${input.device}:${input.prompt('sprint')}`));
  input.update(1 / 60);
  expect(input.device).toBe('keyboard'); expect(input.prompt('sprint')).toBe('Shift');
  expect(input.padFamily, 'Xbox names until a pad shows up').toBe('xbox');

  // A connected DualSense names the start screen's pad list before its first press; a press makes it the device.
  pads = [pad(DUALSENSE)];
  input.update(1 / 60);
  expect(input.padFamily).toBe('playstation'); expect(input.device).toBe('keyboard');
  expect(input.prompt('jump', input.padFamily)).toBe('□');
  const revision = input.promptRevision;
  pads = [pad(DUALSENSE, { 3: 1 })];
  input.update(1 / 60);
  expect(input.device).toBe('playstation');
  expect(input.promptRevision).toBeGreaterThan(revision);
  expect(input.prompt('sprint')).toBe('✕'); expect(input.prompt('interact')).toBe('△'); expect(input.prompt('pause')).toBe('Options');

  const key = (type: string) => events.dispatchEvent(Object.assign(new Event(type), { code: 'KeyW', repeat: false }));
  pads = [pad(DUALSENSE)];
  key('keydown'); input.update(1 / 60); key('keyup');
  expect(input.device).toBe('keyboard'); expect(input.prompt('sprint')).toBe('Shift');

  pads = [pad(XBOX, { 0: 1 })];
  input.update(1 / 60);
  expect(input.device).toBe('xbox'); expect(input.prompt('sprint')).toBe('Ⓐ'); expect(input.prompt('pause')).toBe('Menu');
  pads = [];
  input.update(1 / 60);
  expect(input.device).toBe('keyboard');
  expect(input.padFamily, 'the last pad keeps naming the pad list').toBe('xbox');
  // Subscribe, first update, the DualSense showing up, its press, the key, the Xbox press, the Xbox pad leaving.
  expect(seen).toEqual(['keyboard:Shift', 'keyboard:Shift', 'keyboard:Shift', 'playstation:✕', 'keyboard:Shift', 'xbox:Ⓐ', 'keyboard:Shift']);
  input.dispose();
});

test('button glyphs live only in input/bindings.ts; "Press … to" lines take the device button', () => {
  const root = path.resolve(__dirname, '../../src');
  const glyphs = /[ⒶⒷⓍⓧⓎⓁⓇ✕○□△]/;
  const offenders = fs.readdirSync(root, { recursive: true }).map(String)
    .filter(f => f.endsWith('.ts') && f !== path.join('input', 'bindings.ts'))
    .filter(f => glyphs.test(fs.readFileSync(path.join(root, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')));
  expect(offenders).toEqual([]);
  const presses = DIALOGUE.lines.filter(line => /\bPress\b/.test(line.text));
  expect(presses.length).toBeGreaterThan(0);
  for (const line of presses) expect(line.text, line.id).toContain('Press {button}');
});
