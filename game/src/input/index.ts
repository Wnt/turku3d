import { ACTIONS, DEFAULT_BINDINGS, editorBindings, emptyActions, mapActions, padFamily, promptLabel, clamp, type Action, type Actions, type Binding, type Bindings, type PadFamily, type PromptDevice } from './bindings';
import { TouchOverlay, TouchPad, type TouchContext } from './touch';
export * from './bindings';
export type InputSource = 'keyboard' | 'gamepad' | 'touch';
/** `game`: the default bindings. `editor`: the developer object editor's layer (bindings.ts editorBindings). */
export type InputLayer = 'game' | 'editor';

/** Radians of camera turn per CSS px of mouse movement or look drag. */
const LOOK_PER_PX = 0.0025;
/** Developer panels (ui/devLayers.ts, ui/timePanel.ts): their controls keep their keys and drags. */
const PANELS = '#dev-layers, #time-panel';
/** Developer panel toggles and shortcuts, which work while focus is inside a panel. */
const PANEL_ACTIONS: readonly Action[] = ['layerPanel', 'timePanel', 'timePause', 'timeSpeed1', 'timeSpeed2', 'timeSpeed3',
  'timeSpeed4', 'timeHourBack', 'timeHourForward', 'timeMinutesBack', 'timeMinutesForward', 'timeLive'];
const NO_KEYS: ReadonlySet<string> = new Set();
/** Toggles fullscreen from the keydown listener (not a binding: the request needs the original key gesture). */
export const FULLSCREEN_KEY = 'F10';
const NO_TAPS: Readonly<Actions> = Object.freeze(emptyActions());

/** The only browser input owner. Gameplay sees actions, never keys or device indices. */
export class Input {
  readonly bindings: Bindings = structuredClone(DEFAULT_BINDINGS);
  private editorTable: Bindings | null = null;
  private layerName: InputLayer = 'game';
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
  /** The mouse button went down in a developer panel (a slider drag): its moves never look around. */
  private panelDrag = false;
  private mouseX = 0;
  private mouseY = 0;
  private wheel = 0;
  private pad: Gamepad | null = null;
  private padActive = false;
  /** Button names of the pad seen last; they stay after it disconnects (Xbox until a pad shows up). */
  private family: PadFamily = 'xbox';
  private promptKey = '';
  private promptListeners = new Set<() => void>();
  private revision = 0;
  private focused = true;
  private listeners = new AbortController();
  private sprintUntil = 0;
  private time = 0;
  private readonly touch = new TouchPad();
  private overlay: TouchOverlay | null = null;
  private touchContext: TouchContext = 'hidden';
  /** A program producing actions like a controller (gameplay/autopilot): called every update after the devices are
   *  merged, with the merged actions to read (a human takeover) and overwrite. Edges, sprint and prompts follow. */
  driver: ((actions: Actions, dt: number) => void) | null = null;
  /** The steer action is the held keys' own value (−1, 0 or 1: no stick, touch or driver in it), so a car slews it
   *  as a keyboard steers (VehicleInput.steerKey). */
  steerFromKeys = false;

  constructor(el: HTMLElement, private readonly activate: () => void = () => {}, private readonly toggleFullscreen: () => void = () => {}, private readonly copyPosition: () => void = () => {}) {
    const options = { signal: this.listeners.signal };
    if ((navigator.maxTouchPoints ?? 0) > 0) this.enableTouch();
    addEventListener('keydown', e => {
      // A focused developer panel control keeps its own keys (a slider's arrows); only the panels' keys pass.
      if ((e.target as HTMLElement)?.closest?.(PANELS) && !this.panelKey(e.code)) return;
      if (['AltLeft', 'AltRight', 'Space'].includes(e.code)) e.preventDefault();
      if (e.repeat) return;
      if (e.code === FULLSCREEN_KEY) {
        e.preventDefault();
        this.toggleFullscreen(); // Fullscreen requires the original keyboard gesture, before the frame loop.
        return;
      }
      if (this.bindings.copyPosition.some(b => b.kind === 'key' && b.code === e.code)) this.copyPosition();
      this.tapped.add(e.code); this.activate();
      this.keys.add(e.code); this.source = 'keyboard';
    }, options);
    addEventListener('keyup', e => this.keys.delete(e.code), options);
    addEventListener('blur', () => { this.focused = false; this.clear(); this.touch.clear(); void this.pad?.vibrationActuator?.reset?.(); }, options);
    addEventListener('focus', () => { this.focused = true; }, options);
    addEventListener('gamepaddisconnected', e => { if (this.pad?.index === e.gamepad.index) { this.pad = null; this.clear(); } }, options);
    addEventListener('pointerdown', e => {
      if (e.pointerType === 'touch') this.enableTouch(); else if (e.pointerType === 'mouse') this.source = 'keyboard';
      this.panelDrag = !!(e.target as HTMLElement)?.closest?.(PANELS);
      this.activate();
    }, options);
    addEventListener('pointerup', () => { this.panelDrag = false; }, options);
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
      if (this.panelDrag || (e.target as HTMLElement)?.closest?.(PANELS)) return;
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

  /** The binding layer the next update maps with. */
  get layer(): InputLayer { return this.layerName; }
  set layer(layer: InputLayer) {
    if (layer === this.layerName) return;
    this.layerName = layer;
    this.editorTable = null;
    this.promptsChanged(true);
  }
  /** The bindings of the active layer. */
  get active(): Bindings {
    if (this.layerName === 'game') return this.bindings;
    return this.editorTable ??= editorBindings(this.bindings);
  }

  /** Poll every render frame: Chrome may expose a newly plugged pad only after its first button press. */
  update(dt: number) {
    this.time += dt;
    const bindings = this.active;
    const pads = this.focused ? Array.from(navigator.getGamepads?.() ?? []).filter((p): p is Gamepad => !!p && p.connected && p.mapping === 'standard') : [];
    const active = pads.find(p => {
      const a = mapActions(p, NO_KEYS, bindings);
      return ACTIONS.some(k => Math.abs(a[k]) > 0.01);
    });
    this.pad = active ?? pads.find(p => p.index === this.pad?.index) ?? pads[0] ?? null;
    if (this.pad) this.family = padFamily(this.pad.id);
    if (active) { this.source = 'gamepad'; if (!this.padActive) this.activate(); }
    this.padActive = !!active;
    if (!this.pad && this.source === 'gamepad') this.source = 'keyboard';
    const next = mapActions(this.pad, this.keys, bindings);
    const touch = this.touch.sample();
    if (this.touch.active) this.source = 'touch';
    for (const a of ACTIONS) if (Math.abs(touch.actions[a]) > Math.abs(next[a])) next[a] = touch.actions[a];
    this.driver?.(next, dt);
    let keyed = 0;
    for (const b of bindings.steer) if (b.kind === 'key' && this.keys.has(b.code)) keyed += b.scale ?? 1;
    this.steerFromKeys = this.source === 'keyboard' && next.steer === Math.max(-1, Math.min(1, keyed));
    const taps = this.tapped.size ? mapActions(null, this.tapped, bindings) : NO_TAPS;
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
    this.lookX = next.lookX * 2.4 * dt + (this.mouseX + touch.lookX * (this.touchContext === 'foot' ? 2 : 1)) * LOOK_PER_PX;
    this.lookY = next.lookY * 1.8 * dt + (this.mouseY + touch.lookY) * LOOK_PER_PX;
    this.zoom = this.wheel + touch.zoom;
    this.mouseX = this.mouseY = this.wheel = 0;
    this.overlay?.draw(this.touch.stick);
    this.promptsChanged();
  }
  /** Bumps `promptRevision` and calls the prompt listeners when the prompt device or the pad family changed. */
  private promptsChanged(force = false) {
    const key = `${this.device}|${this.family}`;
    if (!force && key === this.promptKey) return;
    this.promptKey = key;
    this.revision++;
    for (const listener of this.promptListeners) listener();
  }
  /** Which touch buttons to show this frame; the overlay stays hidden unless the player is on touch. */
  setTouchContext(context: TouchContext) {
    this.touchContext = context;
    const shown = this.source === 'touch' ? context : 'hidden';
    if (shown === 'hidden') this.touch.clear();
    this.overlay?.setContext(shown);
  }
  private panelKey(code: string) {
    return PANEL_ACTIONS.some(action => this.bindings[action].some(b => b.kind === 'key' && b.code === code));
  }
  get connected() { return this.pad !== null; }
  get gamepadName() { return this.pad?.id ?? null; }
  /** Button names of the connected pad, or of the last one seen: PlayStation for a Sony controller, else Xbox. */
  get padFamily(): PadFamily { return this.family; }
  /** The device prompts name: the pad family while a pad is in use, else the keyboard or the touch overlay. */
  get device(): PromptDevice { return this.source === 'gamepad' ? this.family : this.source; }
  /** Changes whenever prompts may read differently (device, pad family, bindings, layer): re-render on a change. */
  get promptRevision() { return this.revision; }
  /** Calls `listener` now and after every prompt change (checked once per update); returns the unsubscribe. */
  watchPrompts(listener: () => void): () => void {
    this.promptListeners.add(listener);
    listener();
    return () => { this.promptListeners.delete(listener); };
  }
  get sprinting() { return this.actions.sprint > 0.5 || this.time < this.sprintUntil; }
  consume(action: Action) { const pressed = this.edges.has(action); this.edges.delete(action); return pressed; }
  rebind(action: Action, bindings: Binding[]) { this.bindings[action] = structuredClone(bindings); this.editorTable = null; this.promptsChanged(true); }
  /** The name of `action`'s binding on `device` (default: the device in use) in the active layer (bindings.ts
   *  promptLabel). Every help line, hint and "Press … to" prompt takes its button names from here. */
  prompt(action: Action, device: PromptDevice = this.device) { return promptLabel(action, this.active, device); }
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
