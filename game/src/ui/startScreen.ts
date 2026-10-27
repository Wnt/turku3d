import { glyph, type Action } from '../input/bindings';
import type { Input } from '../input';

interface StartOptions {
  /** Story paragraphs for a new game (dialogue line texts), empty when continuing a saved game. */
  intro?: readonly string[];
  /** One line under the controls, e.g. how to get fullscreen where the browser cannot provide it. */
  hint?: string | null;
  /** Runs synchronously inside the closing gesture (a fullscreen request needs it). */
  onClose?: () => void;
}

/** Controller discovery stays live while the player reads the controls. A new game also shows the story intro. */
export class StartScreen {
  readonly element = document.createElement('section');
  active = true;
  private status: HTMLElement;
  private touchLine: HTMLElement;
  private button: HTMLButtonElement;

  /** Resolves when the player dismisses the screen. */
  readonly closed: Promise<void>;
  private resolveClosed!: () => void;

  private readonly onClose: () => void;

  constructor(input: Input, { intro = [], hint = null, onClose = () => {} }: StartOptions = {}) {
    this.onClose = onClose;
    this.closed = new Promise(resolve => { this.resolveClosed = resolve; });
    this.element.id = 'start-screen';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-labelledby', 'start-title');
    this.element.innerHTML = `<div class="start-card">
      <p class="eyebrow">TURKU · FREE ROAM</p>
      <h1 id="start-title">Läntinen Rantakatu 15</h1>
      <p>Explore on foot. Your car is parked nearby.</p>
      <p id="gamepad-status" role="status"></p>
      <dl id="start-bindings"></dl>
      <p>Keyboard: WASD move · mouse look · Shift sprint · Space jump · E interact<br>F enter/exit · W/S drive/reverse · Space handbrake · M map · Esc pause</p>
      <p class="start-touch">Touch: left thumb moves and drives · drag right to look · pinch to zoom · buttons bottom right</p>
      <p class="start-fullscreen" hidden></p>
      <button type="button" disabled>Start playing</button>
      <p class="start-hint">Loading the world…</p>
      <p class="start-credits">Map data, lidar and aerial imagery © City of Turku, CC BY 4.0 · Streets © OpenStreetMap contributors, ODbL</p>
    </div>`;
    const actions: [Action, string][] = [['moveY', 'Move / steer'], ['lookX', 'Look'], ['sprint', 'Sprint / start'],
      ['jump', 'Jump'], ['mission', 'Interact'], ['interact', 'Enter / exit car'], ['throttle', 'Accelerate'], ['brake', 'Brake / reverse'],
      ['handbrake', 'Handbrake'], ['cameraReset', 'Reset camera'], ['map', 'Map'], ['pause', 'Pause / resume']];
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
    const fullscreen = this.element.querySelector<HTMLElement>('.start-fullscreen')!;
    fullscreen.textContent = hint;
    fullscreen.hidden = !hint;
    this.status = this.element.querySelector('#gamepad-status')!;
    this.touchLine = this.element.querySelector('.start-touch')!;
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
    this.status.textContent = input.gamepadName ? `${input.gamepadName} connected`
      : input.source === 'touch' ? 'Touch controls ready.' : 'No controller detected — connect it and press a button.';
    this.touchLine.hidden = !input.touchDevice;
    if (!this.button.disabled && input.consume('sprint')) this.close();
  }
  private close() {
    if (!this.active) return;
    this.active = false;
    this.element.remove();
    this.onClose();
    this.resolveClosed();
  }
}
