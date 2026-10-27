import { ACTIONS, DEFAULT_BINDINGS, emptyActions, glyph, mapActions, clamp, type Action, type Actions, type Binding, type Bindings } from './bindings';
export * from './bindings';

/** The only browser input owner. Gameplay sees actions, never keys or device indices. */
export class Input {
  readonly bindings: Bindings = structuredClone(DEFAULT_BINDINGS);
  actions: Actions = emptyActions();
  source: 'keyboard' | 'gamepad' = 'keyboard';
  lookX = 0;
  lookY = 0;
  zoom = 0;
  private keys = new Set<string>();
  private tapped = new Set<string>();
  private edges = new Set<Action>();
  private mouseX = 0;
  private mouseY = 0;
  private wheel = 0;
  private pad: Gamepad | null = null;
  private padActive = false;
  private focused = true;
  private listeners = new AbortController();
  private sprintUntil = 0;
  private time = 0;

  constructor(el: HTMLElement, private readonly activate: () => void = () => {}) {
    const options = { signal: this.listeners.signal };
    addEventListener('keydown', e => {
      if (['AltLeft', 'AltRight', 'Space'].includes(e.code)) e.preventDefault();
      if (!e.repeat) { this.tapped.add(e.code); this.activate(); }
      this.keys.add(e.code); this.source = 'keyboard';
    }, options);
    addEventListener('keyup', e => this.keys.delete(e.code), options);
    addEventListener('blur', () => { this.focused = false; this.clear(); void this.pad?.vibrationActuator?.reset?.(); }, options);
    addEventListener('focus', () => { this.focused = true; }, options);
    addEventListener('gamepaddisconnected', e => { if (this.pad?.index === e.gamepad.index) { this.pad = null; this.clear(); } }, options);
    addEventListener('pointerdown', () => this.activate(), options);
    el.addEventListener('click', () => { if (document.pointerLockElement !== el) void el.requestPointerLock?.()?.catch(() => {}); }, options);
    addEventListener('mousemove', e => {
      if (document.pointerLockElement === el || e.buttons & 1) {
        this.mouseX += e.movementX; this.mouseY += e.movementY; this.source = 'keyboard';
      }
    }, options);
    el.addEventListener('wheel', e => { this.wheel += Math.sign(e.deltaY); }, { ...options, passive: true });
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
    if (!this.pad) this.source = 'keyboard';
    const next = mapActions(this.pad, this.keys, this.bindings);
    const taps = mapActions(null, this.tapped, this.bindings);
    this.tapped.clear();
    this.edges.clear();
    // A full key press/release can happen between frames. Its next press is a new edge
    // even if the previous frame still recorded the key as held.
    for (const a of ACTIONS) if (taps[a] > 0.5 || (next[a] > 0.5 && this.actions[a] <= 0.5)) this.edges.add(a);
    // A tap grants a short sprint burst; holding A sustains it. Releasing movement cancels it.
    if (this.edges.has('sprint')) this.sprintUntil = this.time + 0.45;
    if (Math.hypot(next.moveX, next.moveY) < 0.01) this.sprintUntil = 0;
    this.actions = next;
    this.lookX = next.lookX * 2.4 * dt + this.mouseX * 0.0025;
    this.lookY = next.lookY * 1.8 * dt + this.mouseY * 0.0025;
    this.zoom = this.wheel;
    this.mouseX = this.mouseY = this.wheel = 0;
  }
  get connected() { return this.pad !== null; }
  get gamepadName() { return this.pad?.id ?? null; }
  get sprinting() { return this.actions.sprint > 0.5 || this.time < this.sprintUntil; }
  consume(action: Action) { const pressed = this.edges.has(action); this.edges.delete(action); return pressed; }
  rebind(action: Action, bindings: Binding[]) { this.bindings[action] = structuredClone(bindings); }
  prompt(action: Action) { return glyph(action, this.bindings, this.source === 'gamepad'); }
  private clear() { this.keys.clear(); this.tapped.clear(); this.actions = emptyActions(); this.edges.clear(); this.mouseX = this.mouseY = this.wheel = this.sprintUntil = 0; }
  async rumble(duration = 100, strong = 0.35, weak = 0.15): Promise<boolean> {
    const actuator = this.pad?.vibrationActuator;
    if (!actuator?.playEffect) return false;
    try {
      return await actuator.playEffect('dual-rumble', { duration: clamp(duration, 0, 1000), startDelay: 0,
        strongMagnitude: clamp(strong, 0, 1), weakMagnitude: clamp(weak, 0, 1) }) === 'complete';
    } catch { return false; }
  }
  dispose() { this.listeners.abort(); this.clear(); void this.pad?.vibrationActuator?.reset?.(); }
}
