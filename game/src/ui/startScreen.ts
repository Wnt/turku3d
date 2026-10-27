import { glyph, type Action } from '../input/bindings';
import type { Input } from '../input';
import { MenuPanel, type MenuHost, type MenuPage } from './menu';

interface StartOptions {
  /** Story paragraphs for a first game (dialogue line texts), empty when a save exists. */
  intro?: readonly string[];
  /** One line under the controls, e.g. how to get fullscreen where the browser cannot provide it. */
  hint?: string | null;
  /** Runs synchronously inside the closing gesture (a fullscreen request needs it). */
  onClose?: () => void;
}

/**
 * Controller discovery stays live while the player reads the controls; the main menu (Continue, New game, Load game:
 * ui/menu.ts SaveMenuFlow.start) starts the game. A first game also shows the story intro. Quitting a game reopens it.
 */
export class StartScreen implements MenuHost {
  readonly element = document.createElement('section');
  readonly menu: MenuPanel;
  active = true;
  private status: HTMLElement;
  private touchLine: HTMLElement;
  private readonly onClose: () => void;

  constructor(input: Input, { intro = [], hint = null, onClose = () => {} }: StartOptions = {}) {
    this.onClose = onClose;
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
      <p class="start-hint">Loading the world…</p>
      <p class="start-credits">Map data, lidar and aerial imagery © City of Turku, CC BY 4.0 · Streets © OpenStreetMap contributors, ODbL · Scooter model by MiniPoly, CC BY 3.0</p>
    </div>`;
    const actions: [Action, string][] = [['moveY', 'Move / steer'], ['lookX', 'Look'], ['sprint', 'Sprint'],
      ['jump', 'Jump'], ['mission', 'Interact'], ['interact', 'Enter / exit car'], ['throttle', 'Accelerate'], ['brake', 'Brake / reverse'],
      ['handbrake', 'Handbrake'], ['cameraReset', 'Reset camera'], ['map', 'Map'], ['pause', 'Pause menu']];
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
    this.menu = new MenuPanel(input);
    this.menu.setEnabled(false);
    this.status.after(this.menu.element);
    document.body.append(this.element);
  }
  show(page: MenuPage) { this.menu.show(page); }
  /** The world is loaded: the menu accepts picks. */
  ready() {
    this.menu.setEnabled(true);
    this.element.querySelector('.start-hint')!.textContent = 'Press Ⓐ or select an option.';
  }
  update(input: Input) {
    this.status.textContent = input.gamepadName ? `${input.gamepadName} connected`
      : input.source === 'touch' ? 'Touch controls ready.' : 'No controller detected — connect it and press a button.';
    this.touchLine.hidden = !input.touchDevice;
    this.menu.handle(input);
  }
  /** Leaves for the game; call from the pick that starts it (still inside the gesture). */
  close() {
    if (!this.active) return;
    this.active = false;
    this.element.remove();
    this.onClose();
  }
  /** Back from a game: the menu `page` over the world, without the first-game intro. */
  reopen(page: MenuPage) {
    this.element.querySelector('.start-intro')?.remove();
    this.active = true;
    this.show(page);
    document.body.append(this.element);
  }
}
