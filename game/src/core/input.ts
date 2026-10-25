// Keyboard + mouse state. Tests can drive it through window.__game.input (see main.ts).
export class Input {
  readonly keys = new Set<string>();
  /** Synthetic held keys (tests): merged with real ones. */
  readonly forced = new Set<string>();
  private pressed = new Set<string>();
  mouseDX = 0;
  mouseDY = 0;
  wheel = 0;

  constructor(private el: HTMLElement) {
    addEventListener('keydown', (e) => {
      if (e.code === 'AltLeft' || e.code === 'AltRight' || e.code === 'Space') e.preventDefault();
      if (!e.repeat) this.pressed.add(e.code);
      this.keys.add(e.code);
    });
    addEventListener('keyup', (e) => this.keys.delete(e.code));
    addEventListener('blur', () => this.keys.clear());
    el.addEventListener('click', () => { if (document.pointerLockElement !== el) el.requestPointerLock?.()?.catch?.(() => {}); });
    addEventListener('mousemove', (e) => {
      if (document.pointerLockElement === this.el || e.buttons & 1) { this.mouseDX += e.movementX; this.mouseDY += e.movementY; }
    });
    el.addEventListener('wheel', (e) => { this.wheel += Math.sign(e.deltaY); }, { passive: true });
  }
  down(code: string): boolean { return this.keys.has(code) || this.forced.has(code); }
  /** True once per physical press. */
  consume(code: string): boolean { const had = this.pressed.has(code); this.pressed.delete(code); return had; }
  press(code: string): void { this.pressed.add(code); }
  endFrame(): void { this.mouseDX = this.mouseDY = this.wheel = 0; this.pressed.clear(); }
}
