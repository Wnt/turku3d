import { ACTIONS, DEFAULT_BINDINGS, emptyActions, glyph, mapActions, clamp, type Action, type Actions, type Binding, type Bindings } from './bindings';
import { TouchOverlay, TouchPad, type TouchContext } from './touch';
export * from './bindings';
export type InputSource = 'keyboard' | 'gamepad' | 'touch';

/** Radians of camera turn per CSS px of mouse movement or look drag. */
const LOOK_PER_PX = 0.0025;

/** The only browser input owner. Gameplay sees actions, never keys or device indices. */
export class Input {
  readonly bindings: Bindings = structuredClone(DEFAULT_BINDINGS);
  actions: Actions = emptyActions();
  source: InputSource = 'keyboard';
  /** A touch screen was reported (`navigator.maxTouchPoints`) or touched. The overlay shows while `source` is touch. */
  touchDevice = false;
  lookX = 0;
  lookY = 0;
  zoom = 0;
  private keys = new Set<string>();
  private tapped = new Set<string>();
  private edges = new Set<Action>();
  /** Non-consuming press activity for help learning, including either axis direction and mouse look starts. */
  readonly activity = new Set<Action>();
  private mouseLooking = false;
  private mouseX = 0;
  private mouseY = 0;
  private wheel = 0;
  private pad: Gamepad | null = null;
  private padActive = false;
  private focused = true;
  private listeners = new AbortController();
  private sprintUntil = 0;
  private time = 0;
  private readonly touch = new TouchPad();
  private overlay: TouchOverlay | null = null;

  constructor(el: HTMLElement, private readonly activate: () => void = () => {}, private readonly toggleFullscreen: () => void = () => {}) {
    const options = { signal: this.listeners.signal };
    if ((navigator.maxTouchPoints ?? 0) > 0) this.enableTouch();
    addEventListener('keydown', e => {
      if (['AltLeft', 'AltRight', 'Space'].includes(e.code)) e.preventDefault();
      if (e.repeat) return;
      if (e.code === 'F10') {
        e.preventDefault();
        this.toggleFullscreen(); // Fullscreen requires the original keyboard gesture, before the frame loop.
        return;
      }
      this.tapped.add(e.code); this.activate();
      this.keys.add(e.code); this.source = 'keyboard';
    }, options);
    addEventListener('keyup', e => this.keys.delete(e.code), options);
    addEventListener('blur', () => { this.focused = false; this.clear(); this.touch.clear(); void this.pad?.vibrationActuator?.reset?.(); }, options);
    addEventListener('focus', () => { this.focused = true; }, options);
    addEventListener('gamepaddisconnected', e => { if (this.pad?.index === e.gamepad.index) { this.pad = null; this.clear(); } }, options);
    addEventListener('pointerdown', e => {
      if (e.pointerType === 'touch') this.enableTouch(); else if (e.pointerType === 'mouse') this.source = 'keyboard';
      this.activate();
    }, options);
    // Touch: the game element is the stick (left half) and look/pinch (right half) area; buttons are in the overlay.
    // Touch pointers are captured by their pointerdown target, so moves and lifts arrive on the same element.
    // index.html gives the canvas `touch-action: none`, so the browser neither scrolls nor zooms on these drags.
    el.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'touch') return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      this.touch.down(e.pointerId, e.clientX - r.left, e.clientY - r.top, r.width);
    }, options);
    el.addEventListener('pointermove', e => {
      if (e.pointerType !== 'touch') return;
      const r = el.getBoundingClientRect();
      this.touch.move(e.pointerId, e.clientX - r.left, e.clientY - r.top);
    }, options);
    for (const type of ['pointerup', 'pointercancel'] as const) el.addEventListener(type, e => this.touch.up(e.pointerId), options);
    el.addEventListener('click', e => {
      if ((e as PointerEvent).pointerType !== 'touch' && document.pointerLockElement !== el) void el.requestPointerLock?.()?.catch(() => {});
    }, options);
    addEventListener('mousemove', e => {
      if (document.pointerLockElement === el || e.buttons & 1) {
        this.mouseX += e.movementX; this.mouseY += e.movementY; this.source = 'keyboard';
      }
    }, options);
    el.addEventListener('wheel', e => { this.wheel += Math.sign(e.deltaY); }, { ...options, passive: true });
  }

  /** Creates the thumb-button overlay on the first sign of a touch screen and makes touch the active source. */
  private enableTouch() {
    this.source = 'touch';
    if (this.overlay) return;
    this.touchDevice = true;
    const overlay = this.overlay = new TouchOverlay(document.body, this.bindings);
    const options = { signal: this.listeners.signal };
    overlay.element.addEventListener('pointerdown', e => {
      const action = overlay.actionOf(e.target);
      if (!action) return;
      e.preventDefault();
      this.touch.press(e.pointerId, action);
    }, options);
    for (const type of ['pointerup', 'pointercancel'] as const) overlay.element.addEventListener(type, e => this.touch.up(e.pointerId), options);
  }

  /** Poll every render frame: Chrome may expose a newly plugged pad only after its first button press. */
  update(dt: number) {
    this.time += dt;
    const pads = this.focused ? Array.from(navigator.getGamepads?.() ?? []).filter((p): p is Gamepad => !!p && p.connected && p.mapping === 'standard') : [];
    const active = pads.find(p => {
      const a = mapActions(p, new Set(), this.bindings);
      return ACTIONS.some(k => Math.abs(a[k]) > 0.01);
    });
    this.pad = active ?? pads.find(p => p.index === this.pad?.index) ?? pads[0] ?? null;
    if (active) { this.source = 'gamepad'; if (!this.padActive) this.activate(); }
    this.padActive = !!active;
    if (!this.pad && this.source === 'gamepad') this.source = 'keyboard';
    const next = mapActions(this.pad, this.keys, this.bindings);
    const touch = this.touch.sample();
    if (this.touch.active) this.source = 'touch';
    for (const a of ACTIONS) if (Math.abs(touch.actions[a]) > Math.abs(next[a])) next[a] = touch.actions[a];
    const taps = mapActions(null, this.tapped, this.bindings);
    this.tapped.clear();
    this.edges.clear();
    this.activity.clear();
    for (const a of ACTIONS) if (Math.abs(taps[a]) > 0.5 || touch.taps.has(a) ||
      (Math.abs(next[a]) > 0.5 && Math.abs(this.actions[a]) <= 0.5)) this.activity.add(a);
    const mouseLooking = !!(this.mouseX || this.mouseY || touch.lookX || touch.lookY);
    if (mouseLooking && !this.mouseLooking) this.activity.add('lookX');
    this.mouseLooking = mouseLooking;
    // A full key press/release (or button tap) can happen between frames. Its next press is a new edge
    // even if the previous frame still recorded the key as held.
    for (const a of ACTIONS) if (taps[a] > 0.5 || touch.taps.has(a) || (next[a] > 0.5 && this.actions[a] <= 0.5)) this.edges.add(a);
    // A tap grants a short sprint burst; holding A sustains it. Releasing movement cancels it.
    if (this.edges.has('sprint')) this.sprintUntil = this.time + 0.45;
    if (Math.hypot(next.moveX, next.moveY) < 0.01) this.sprintUntil = 0;
    this.actions = next;
    this.lookX = next.lookX * 2.4 * dt + (this.mouseX + touch.lookX) * LOOK_PER_PX;
    this.lookY = next.lookY * 1.8 * dt + (this.mouseY + touch.lookY) * LOOK_PER_PX;
    this.zoom = this.wheel + touch.zoom;
    this.mouseX = this.mouseY = this.wheel = 0;
    this.overlay?.draw(this.touch.stick);
  }
  /** Which touch buttons to show this frame; the overlay stays hidden unless the player is on touch. */
  setTouchContext(context: TouchContext) {
    const shown = this.source === 'touch' ? context : 'hidden';
    if (shown === 'hidden') this.touch.clear();
    this.overlay?.setContext(shown);
  }
  get connected() { return this.pad !== null; }
  get gamepadName() { return this.pad?.id ?? null; }
  get sprinting() { return this.actions.sprint > 0.5 || this.time < this.sprintUntil; }
  consume(action: Action) { const pressed = this.edges.has(action); this.edges.delete(action); return pressed; }
  rebind(action: Action, bindings: Binding[]) { this.bindings[action] = structuredClone(bindings); }
  /** Touch buttons carry the gamepad glyphs, so touch prompts use them too. */
  prompt(action: Action) { return glyph(action, this.bindings, this.source !== 'keyboard'); }
  private clear() { this.keys.clear(); this.tapped.clear(); this.actions = emptyActions(); this.edges.clear(); this.activity.clear(); this.mouseLooking = false; this.mouseX = this.mouseY = this.wheel = this.sprintUntil = 0; }
  async rumble(duration = 100, strong = 0.35, weak = 0.15): Promise<boolean> {
    const actuator = this.pad?.vibrationActuator;
    if (!actuator?.playEffect) return false;
    try {
      return await actuator.playEffect('dual-rumble', { duration: clamp(duration, 0, 1000), startDelay: 0,
        strongMagnitude: clamp(strong, 0, 1), weakMagnitude: clamp(weak, 0, 1) }) === 'complete';
    } catch { return false; }
  }
  dispose() { this.listeners.abort(); this.clear(); this.touch.clear(); this.overlay?.dispose(); void this.pad?.vibrationActuator?.reset?.(); }
}
