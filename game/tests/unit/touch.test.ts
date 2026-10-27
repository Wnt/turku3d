import { afterEach, describe, expect, test, vi } from 'vitest';
import { Input, MOVE_CURVE, radial } from '../../src/input';
import { STICK_RADIUS, TOUCH_BUTTONS, TouchPad } from '../../src/input/touch';
import { fullscreenHint, guardPause, mustBlock, type FullscreenFacts } from '../../src/ui/fullscreen';

afterEach(() => vi.unstubAllGlobals());
const W = 844;

describe('touch pad', () => {
  test('the floating stick maps through the gamepad radial curve to move, steer, throttle and brake', () => {
    const pad = new TouchPad();
    pad.down(1, 150, 250, W);
    expect(pad.sample().actions.moveY).toBe(0);
    pad.move(1, 150, 250 - STICK_RADIUS * 0.5);
    const half = pad.sample().actions;
    expect(half.moveY).toBeCloseTo(radial(0, 0.5, MOVE_CURVE)[1]);
    expect(half.moveX).toBe(0);
    expect(half.throttle).toBe(half.moveY); expect(half.brake).toBe(0);
    pad.move(1, 150, 250 + STICK_RADIUS);
    const back = pad.sample().actions;
    expect(back.moveY).toBeCloseTo(-1); expect(back.brake).toBeCloseTo(1); expect(back.throttle).toBe(0);
    pad.move(1, 150 + STICK_RADIUS * 0.1, 250);
    expect(pad.sample().actions.moveX, 'inside the dead zone').toBe(0);
    // Beyond the radius the base follows the thumb, so pulling back right away reverses at once.
    pad.move(1, 150 + STICK_RADIUS * 3, 250);
    const right = pad.sample().actions;
    expect(right.moveX).toBeCloseTo(1); expect(right.steer).toBe(right.moveX);
    expect(pad.stick).toEqual({ ox: 150 + STICK_RADIUS * 2, oy: 250, x: 150 + STICK_RADIUS * 3, y: 250 });
    pad.move(1, 150 + STICK_RADIUS * 2, 250);
    expect(pad.sample().actions.moveX).toBe(0);
    pad.up(1);
    expect(pad.stick).toBeNull();
    expect(pad.sample().actions.moveX).toBe(0);
  });

  test('look drags report pixel deltas once and a two-finger pinch zooms instead of looking', () => {
    const pad = new TouchPad();
    pad.down(2, 600, 200, W);
    pad.move(2, 560, 210); pad.move(2, 540, 215);
    const look = pad.sample();
    expect([look.lookX, look.lookY]).toEqual([-60, 15]);
    expect(pad.sample().lookX, 'deltas reset after each sample').toBe(0);
    pad.down(3, 700, 215, W);
    pad.move(3, 860, 215); // spread from 160 px to 320 px apart: zoom in
    const pinch = pad.sample();
    expect(pinch.lookX).toBe(0);
    expect(pinch.zoom).toBeCloseTo(-Math.log(2) * 10);
    pad.move(3, 640, 215); // closer again: zoom out
    expect(pad.sample().zoom).toBeGreaterThan(0);
    pad.up(3);
    pad.move(2, 550, 215);
    expect(pad.sample().lookX, 'the remaining finger looks again').toBe(10);
  });

  test('buttons hold until lifted and a tap between frames is still an edge', () => {
    const pad = new TouchPad();
    pad.press(7, 'sprint');
    let f = pad.sample();
    expect(f.actions.sprint).toBe(1); expect(f.taps.has('sprint')).toBe(true);
    f = pad.sample();
    expect(f.actions.sprint).toBe(1); expect(f.taps.has('sprint')).toBe(false);
    pad.up(7);
    expect(pad.sample().actions.sprint).toBe(0);
    pad.press(8, 'jump'); pad.up(8);
    f = pad.sample();
    expect(f.actions.jump).toBe(0); expect(f.taps.has('jump')).toBe(true);
    expect(pad.active).toBe(false);
  });

  test('stick, look and a button work at once; extra fingers are ignored and pointer ids stay separate', () => {
    const pad = new TouchPad();
    pad.down(1, 100, 300, W);
    pad.down(4, 120, 280, W); // a second thumb on the stick half is ignored
    pad.down(2, 600, 200, W);
    pad.press(3, 'mission');
    pad.move(1, 100, 300 - STICK_RADIUS);
    pad.move(4, 0, 0);
    pad.move(2, 650, 200);
    const f = pad.sample();
    expect(f.actions.moveY).toBeCloseTo(1); expect(f.lookX).toBe(50); expect(f.actions.mission).toBe(1);
    pad.up(2);
    expect(pad.sample().actions.moveY, 'lifting the look finger keeps the stick').toBeCloseTo(1);
    pad.up(1);
    const left = pad.sample();
    expect(left.actions.moveY).toBe(0); expect(left.actions.mission).toBe(1);
    pad.down(5, 300, 300, W);
    expect(pad.stick, 'the stick half is free again').not.toBeNull();
    pad.clear();
    expect(pad.active).toBe(false);
  });

  test('every context but hidden has a pause or back button', () => {
    for (const context of ['foot', 'car', 'menu', 'paused'] as const) {
      expect(TOUCH_BUTTONS.some(b => b.contexts.includes(context) && (b.action === 'pause' || b.action === 'menuBack'))).toBe(true);
    }
    expect(TOUCH_BUTTONS.filter(b => b.contexts.includes('car')).map(b => b.action)).toContain('handbrake');
  });
});

test('Input merges touch into the same actions, edges, look and source', () => {
  const events = new EventTarget();
  const el = Object.assign(new EventTarget(), { getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: 390 }) });
  vi.stubGlobal('addEventListener', events.addEventListener.bind(events));
  vi.stubGlobal('navigator', { getGamepads: () => [] });
  const input = new Input(el as unknown as HTMLElement);
  expect(input.touchDevice).toBe(false);
  const pointer = (type: string, pointerId: number, clientX: number, clientY: number) =>
    el.dispatchEvent(Object.assign(new Event(type), { pointerType: 'touch', pointerId, clientX, clientY }));
  pointer('pointerdown', 1, 150, 250);
  pointer('pointermove', 1, 150, 250 - STICK_RADIUS);
  pointer('pointerdown', 2, 600, 200);
  pointer('pointermove', 2, 640, 200);
  input.update(1 / 60);
  expect(input.source).toBe('touch');
  expect(input.actions.moveY).toBeCloseTo(1); expect(input.actions.throttle).toBeCloseTo(1);
  expect(input.lookX).toBeCloseTo(40 * 0.0025, 6);
  expect(input.prompt('mission')).toBe('Ⓐ');
  pointer('pointerup', 1, 150, 250 - STICK_RADIUS);
  input.update(1 / 60);
  expect(input.actions.moveY).toBe(0); expect(input.lookX).toBe(0);
  pointer('pointerup', 2, 640, 200);
  const key = (type: string) => events.dispatchEvent(Object.assign(new Event(type), { code: 'KeyW', repeat: false }));
  key('keydown'); input.update(1 / 60);
  expect(input.source, 'a key press takes over from touch').toBe('keyboard');
  key('keyup');
  input.dispose();
});

describe('fullscreen policy', () => {
  const facts = (f: Partial<FullscreenFacts> = {}): FullscreenFacts =>
    ({ touch: true, inScene: true, fullscreen: false, standalone: false, supported: true, ...f });
  test('only a touch player in the scene outside fullscreen is blocked', () => {
    expect(mustBlock(facts())).toBe(true);
    expect(mustBlock(facts({ touch: false })), 'keyboard and gamepad are untouched').toBe(false);
    expect(mustBlock(facts({ inScene: false })), 'the start screen works without fullscreen').toBe(false);
    expect(mustBlock(facts({ fullscreen: true }))).toBe(false);
    expect(mustBlock(facts({ standalone: true })), 'a Home Screen app counts as fullscreen').toBe(false);
    expect(mustBlock(facts({ supported: false })), 'iPhone Safari continues with the hint').toBe(false);
  });
  test('blocking pauses; unblocking resumes only the policy\'s own pause', () => {
    let s = guardPause(false, false, true);
    expect(s).toEqual({ paused: true, own: true });
    s = guardPause(s.paused, s.own, true);
    expect(s).toEqual({ paused: true, own: true });
    expect(guardPause(s.paused, s.own, false)).toEqual({ paused: false, own: false });
    expect(guardPause(true, false, true), 'a player pause stays the player\'s').toEqual({ paused: true, own: false });
    expect(guardPause(true, false, false)).toEqual({ paused: true, own: false });
    expect(guardPause(false, false, false)).toEqual({ paused: false, own: false });
  });
  test('the Home Screen hint shows only on touch devices without element fullscreen', () => {
    expect(fullscreenHint(true, false, false)).toMatch(/Home Screen/);
    expect(fullscreenHint(true, true, false)).toBeNull();
    expect(fullscreenHint(true, false, true)).toBeNull();
    expect(fullscreenHint(false, false, false)).toBeNull();
  });
});
