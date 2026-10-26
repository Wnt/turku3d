import { glyph, type Action } from '../input/bindings';
import type { Input } from '../input';

/** Controller discovery stays live while the player reads the controls. A new game also shows the story intro. */
export class StartScreen {
  readonly element = document.createElement('section');
  active = true;
  private status: HTMLElement;
  private button: HTMLButtonElement;

  /** `intro`: story paragraphs for a new game (dialogue line texts), empty when continuing; `onClose` runs once. */
  constructor(input: Input, intro: readonly string[] = [], private readonly onClose: () => void = () => {}) {
    this.element.id = 'start-screen';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-labelledby', 'start-title');
    this.element.innerHTML = `<div class="start-card">
      <p class="eyebrow">TURKU · FREE ROAM</p>
      <h1 id="start-title">Läntinen Rantakatu 15</h1>
      <p>Explore on foot. Your car is parked nearby.</p>
      <p id="gamepad-status" role="status"></p>
      <dl id="start-bindings"></dl>
      <p>Keyboard: WASD move · mouse look · Shift sprint · Space jump · E interact<br>F enter/exit · W/S drive/reverse · Space handbrake · Esc pause</p>
      <button type="button" disabled>Start playing</button>
      <p class="start-hint">Loading the world…</p>
    </div>`;
    const actions: [Action, string][] = [['moveY', 'Move / steer'], ['lookX', 'Look'], ['sprint', 'Sprint / start'],
      ['jump', 'Jump'], ['mission', 'Interact'], ['interact', 'Enter / exit car'], ['throttle', 'Accelerate'], ['brake', 'Brake / reverse'],
      ['handbrake', 'Handbrake'], ['cameraReset', 'Reset camera'], ['pause', 'Pause / resume']];
    for (const [action, label] of actions) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = glyph(action, input.bindings, true); dd.textContent = label;
      this.element.querySelector('dl')!.append(dt, dd);
    }
    if (intro.length) {
      const story = document.createElement('div');
      story.className = 'start-intro';
      for (const text of intro) story.append(Object.assign(document.createElement('p'), { textContent: text }));
      this.element.querySelector('h1')!.after(story);
    }
    this.status = this.element.querySelector('#gamepad-status')!;
    this.button = this.element.querySelector('button')!;
    this.button.addEventListener('click', () => this.close());
    document.body.append(this.element);
  }
  ready() {
    this.button.disabled = false;
    this.element.querySelector('.start-hint')!.textContent = 'Press Ⓐ or select Start playing.';
    this.button.focus();
  }
  update(input: Input) {
    this.status.textContent = input.gamepadName ? `${input.gamepadName} connected` : 'No controller detected — connect it and press a button.';
    if (!this.button.disabled && input.consume('sprint')) this.close();
  }
  private close() {
    if (!this.active) return;
    this.active = false;
    this.element.remove();
    this.onClose();
  }
}
