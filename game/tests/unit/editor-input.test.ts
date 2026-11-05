import { afterEach, describe, expect, test, vi } from 'vitest';
import { DEFAULT_BINDINGS, Input, editorBindings, mapActions, promptLabel } from '../../src/input';
import { EDITOR, EditorMachine, type Command, type EditorFrame } from '../../src/dev/editor/state';

/** Standard-mapping pads as Chrome reports them: the DualSense has 18 buttons (16 PS, 17 touchpad), the Xbox 17. */
function pad(kind: 'dualsense' | 'xbox', buttons: Record<number, number> = {}, axes = [0, 0, 0, 0]): Gamepad {
  const id = kind === 'dualsense' ? 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)' : 'Xbox Elite Wireless Controller Series 2 (STANDARD GAMEPAD Vendor: 045e Product: 0b00)';
  return { id, index: 0, connected: true, mapping: 'standard', timestamp: 0, axes,
    buttons: Array.from({ length: kind === 'dualsense' ? 18 : 17 }, (_, i) => ({ value: buttons[i] ?? 0, pressed: (buttons[i] ?? 0) > 0.5, touched: false })),
  } as unknown as Gamepad;
}
afterEach(() => vi.unstubAllGlobals());

function mockInput() {
  let pads: (Gamepad | null)[] = [];
  const events = new EventTarget();
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => pads });
  const input = new Input(new EventTarget() as HTMLElement);
  return { input, set: (p: Gamepad | null) => { pads = [p]; } };
}

/** One editor frame from the Input, as ObjectEditor.handle builds it. */
function frame(input: Input, dt = 1 / 60): EditorFrame {
  const a = input.actions;
  return { dt, toggle: input.consume('editToggle') || a.editToggle > 0.5, pick: input.consume('editPick'), back: input.consume('editBack'),
    prev: input.consume('editPrev'), next: input.consume('editNext'), delete: a.editDelete > 0.5, ground: input.consume('editGround'),
    turnLeft: a.editTurnLeft, turnRight: a.editTurnRight, up: input.consume('editUp'), down: input.consume('editDown'),
    left: input.consume('editLeft'), right: input.consume('editRight'), lidar: a.editLidar > 0.5, moveX: a.moveX, moveY: a.moveY };
}

describe('editor bindings', () => {
  test('the game layer is unchanged for the Xbox pad: editor actions stay unbound except the toggle', () => {
    const a = mapActions(pad('xbox', { 0: 1, 1: 1, 3: 1, 4: 1, 5: 1 }), new Set(), DEFAULT_BINDINGS);
    expect(a.sprint).toBe(1); expect(a.mission).toBe(1); expect(a.vehicleReset).toBe(1); expect(a.interact).toBe(1);
    expect(a.layerPanel).toBe(1); expect(a.handbrake).toBe(1);
    expect(a.editPick + a.editBack + a.editDelete + a.editPrev + a.editNext + a.editToggle).toBe(0);
  });

  test('touchpad click (DualSense), L3+R3 (Xbox) and F8 toggle; L3 alone does not', () => {
    expect(mapActions(pad('dualsense', { 17: 1 }), new Set(), DEFAULT_BINDINGS).editToggle).toBe(1);
    expect(mapActions(pad('xbox', { 10: 1, 11: 1 }), new Set(), DEFAULT_BINDINGS).editToggle).toBe(1);
    expect(mapActions(pad('xbox', { 10: 1 }), new Set(), DEFAULT_BINDINGS).editToggle).toBe(0);
    expect(mapActions(null, new Set(['F8']), DEFAULT_BINDINGS).editToggle).toBe(1);
  });

  test('the editor layer takes ✕ ○ △ L1 R1 Create and the D-pad from gameplay; flight keeps the sticks, □, L2/R2 and gets L3 as fast', () => {
    const b = editorBindings(DEFAULT_BINDINGS);
    const a = mapActions(pad('dualsense', { 0: 1, 1: 1, 3: 1, 4: 1, 5: 1, 8: 1, 12: 1 }), new Set(), b);
    expect([a.editPick, a.editBack, a.editDelete, a.editPrev, a.editNext, a.editLidar, a.editUp]).toEqual([1, 1, 1, 1, 1, 1, 1]);
    for (const action of ['sprint', 'mission', 'vehicleReset', 'interact', 'respawn', 'layerPanel', 'handbrake', 'map', 'lights', 'menuUp'] as const) expect(a[action]).toBe(0);
    const fly = mapActions(pad('dualsense', { 2: 1, 6: 0.4, 7: 0.9, 10: 1 }, [0.5, -1, 0.3, 0]), new Set(), b);
    expect(fly.jump).toBe(1); expect(fly.flyUp).toBe(1); expect(fly.flyDown).toBeCloseTo(0.4); expect(fly.sprint).toBe(1);
    expect(fly.editTurnRight).toBeCloseTo(0.9); expect(fly.editGround).toBe(1); expect(fly.moveY).toBeGreaterThan(0.5); expect(fly.lookX).toBeGreaterThan(0);
    expect(mapActions(pad('dualsense', { 9: 1, 11: 1 }), new Set(), b).pause).toBe(1);
    expect(mapActions(pad('dualsense', { 9: 1, 11: 1 }), new Set(), b).cameraReset).toBe(1);
  });

  test('PlayStation glyphs for a DualSense, Xbox glyphs otherwise', () => {
    const b = editorBindings(DEFAULT_BINDINGS);
    expect(promptLabel('editPick', b, 'playstation')).toBe('✕'); expect(promptLabel('editPick', b, 'xbox')).toBe('Ⓐ');
    expect(promptLabel('editToggle', b, 'playstation')).toBe('Touchpad'); expect(promptLabel('editToggle', b, 'xbox')).toBe('L3+R3');
    expect(promptLabel('editDelete', b, 'playstation')).toBe('△'); expect(promptLabel('editToggle', b, 'keyboard')).toBe('Y');
    expect(promptLabel('editPick', b, 'keyboard')).toBe('Enter');
  });
});

describe('editor state machine through a mocked Gamepad API', () => {
  test('a DualSense session: enter, pick, move, turn, drop, reason, undo, delete hold and close', () => {
    const { input, set } = mockInput();
    const machine = new EditorMachine();
    const log: Command[] = [];
    const run = (p: Gamepad | null, frames = 1, dt = 1 / 60) => {
      set(p);
      for (let i = 0; i < frames; i++) {
        input.update(dt);
        log.push(...machine.step(frame(input, dt)));
        input.layer = machine.active ? 'editor' : 'game';
      }
    };
    const types = () => log.splice(0).map(c => c.type);
    run(pad('dualsense', { 17: 1 })); expect(types()).toEqual(['enter']);
    run(pad('dualsense')); expect(types()).toEqual([]);
    expect(input.layer).toBe('editor');
    // ✕ picks (the editor reports it; holding is set by the editor when a candidate exists).
    run(pad('dualsense', { 0: 1 })); expect(types()).toEqual(['pick']);
    machine.setHolding(true);
    run(pad('dualsense'));
    run(pad('dualsense', {}, [0, -1, 0, 0]), 30);
    const moves = log.splice(0).filter(c => c.type === 'move') as Extract<Command, { type: 'move' }>[];
    expect(moves.length).toBe(30);
    expect(moves.reduce((s, m) => s + m.forward, 0)).toBeCloseTo(0.5, 1);
    run(pad('dualsense', { 5: 1 }));
    const turn = log.splice(0).find(c => c.type === 'turn') as Extract<Command, { type: 'turn' }>;
    expect(turn.radians).toBeCloseTo(-EDITOR.step);
    run(pad('dualsense', { 7: 1 }), 60);
    const smooth = log.splice(0).filter(c => c.type === 'turn').reduce((s, c) => s + (c as Extract<Command, { type: 'turn' }>).radians, 0);
    expect(smooth).toBeCloseTo(-Math.PI / 2, 1);
    run(pad('dualsense')); run(pad('dualsense', { 0: 1 })); expect(types()).toEqual(['drop']);
    machine.setHolding(false); machine.committed();
    run(pad('dualsense')); run(pad('dualsense', { 13: 1 })); expect(log.splice(0)).toEqual([{ type: 'reason', index: 2 }]);
    run(pad('dualsense')); run(pad('dualsense', { 1: 1 })); expect(types()).toEqual(['undo']);
    run(pad('dualsense'));
    // △ held 1 s deletes once; releasing early does nothing.
    run(pad('dualsense', { 3: 1 }), 30); expect(types()).toEqual([]);
    run(pad('dualsense')); run(pad('dualsense', { 3: 1 }), 62); expect(types()).toEqual(['delete']);
    run(pad('dualsense'));
    // A reason window expires after 2 s.
    machine.committed(); run(pad('dualsense'), 130); run(pad('dualsense', { 12: 1 })); expect(types()).toEqual([]);
    run(pad('dualsense'));
    // A short touchpad press leaves; a long one (after entering again) closes the session.
    run(pad('dualsense', { 17: 1 }), 5); run(pad('dualsense')); expect(types()).toEqual(['exit']); expect(machine.active).toBe(false);
    expect(input.layer).toBe('game');
    run(pad('dualsense', { 17: 1 })); run(pad('dualsense')); expect(types()).toEqual(['enter']);
    run(pad('dualsense', { 17: 1 }), 95); expect(types()).toEqual(['closeSession', 'exit']);
    run(pad('dualsense')); expect(types()).toEqual([]);
    input.dispose();
  });

  test('Xbox fallback: L3+R3 enters; LB/RB cycle; while holding, held D-pad ↑ repeats height', () => {
    const { input, set } = mockInput();
    const machine = new EditorMachine();
    const log: Command[] = [];
    const run = (p: Gamepad, frames = 1, dt = 1 / 60) => {
      set(p);
      for (let i = 0; i < frames; i++) {
        input.update(dt);
        log.push(...machine.step(frame(input, dt)));
        const h = machine.height(input.actions.editUp > 0.5, input.actions.editDown > 0.5, dt);
        if (h) log.push(h);
        input.layer = machine.active ? 'editor' : 'game';
      }
    };
    run(pad('xbox', { 10: 1, 11: 1 })); run(pad('xbox'));
    expect(log.splice(0).map(c => c.type)).toEqual(['enter']);
    run(pad('xbox', { 4: 1 })); run(pad('xbox')); run(pad('xbox', { 5: 1 })); run(pad('xbox'));
    expect(log.splice(0)).toEqual([{ type: 'cycle', step: -1 }, { type: 'cycle', step: 1 }]);
    machine.setHolding(true);
    run(pad('xbox', { 12: 1 }), 60); // 1 s held: one step at once, repeats after 0.35 s every 0.12 s
    const steps = log.splice(0).filter(c => c.type === 'height');
    expect(steps.length).toBeGreaterThanOrEqual(6); expect(steps.length).toBeLessThanOrEqual(8);
    input.dispose();
  });

  test('a keyboard tap of F8 between frames still toggles', () => {
    const events = new EventTarget();
    vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
    vi.stubGlobal('navigator', { getGamepads: () => [] });
    const input = new Input(new EventTarget() as HTMLElement);
    const machine = new EditorMachine();
    const key = (type: string) => { const e = new Event(type); Object.assign(e, { code: 'F8', repeat: false }); events.dispatchEvent(e); };
    key('keydown'); key('keyup'); input.update(0.016);
    expect(machine.step(frame(input)).map(c => c.type)).toEqual(['enter']);
    input.update(0.016); machine.step(frame(input));
    key('keydown'); key('keyup'); input.update(0.016); expect(machine.step(frame(input))).toEqual([]);
    input.update(0.016); expect(machine.step(frame(input)).map(c => c.type)).toEqual(['exit']);
    input.dispose();
  });
});

test('holding Create adjusts the point cloud (D-pad, L1/R1) without toggling it; a plain press toggles', () => {
  const machine = new EditorMachine();
  machine.active = true;
  const f = (over: Partial<EditorFrame>): EditorFrame => ({ dt: 1 / 60, toggle: false, pick: false, back: false, prev: false, next: false, delete: false,
    ground: false, turnLeft: 0, turnRight: 0, up: false, down: false, left: false, right: false, lidar: false, moveX: 0, moveY: 0, ...over });
  expect(machine.step(f({ lidar: true }))).toEqual([]);
  expect(machine.step(f({ lidar: true, up: true }))).toEqual([{ type: 'lidar', opacity: 0.1 }]);
  expect(machine.step(f({ lidar: true, next: true }))).toEqual([{ type: 'lidar', colour: 1 }]);
  expect(machine.step(f({ lidar: true, prev: true }))).toEqual([{ type: 'lidar', colour: -1 }]); // not a candidate cycle
  expect(machine.step(f({}))).toEqual([]);
  machine.step(f({ lidar: true }));
  expect(machine.step(f({}))).toEqual([{ type: 'lidar', toggle: true }]);
});

test('the point cloud shows only in the editor mode, as last chosen there, unless the URL pinned it', async () => {
  const { lidarVisibility } = await import('../../src/dev/editor/pointCloudHook');
  expect(lidarVisibility(false, true, false)).toBe(false); // leaving the mode hides it
  expect(lidarVisibility(true, true, false)).toBe(true);   // entering restores the last choice
  expect(lidarVisibility(true, false, false)).toBe(false);
  expect(lidarVisibility(false, false, true)).toBeNull();  // ?pointcloud=1: left alone outside the mode
  expect(lidarVisibility(true, true, true)).toBe(true);
});
