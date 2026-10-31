import { devToolsEnabled } from '../core/localOnly';
import { glyph, type Action } from '../input/bindings';
import type { Input } from '../input';
import { MenuPanel, type MenuHost, type MenuPage } from './menu';

interface StartOptions {
  /** One line under the controls, e.g. how to get fullscreen where the browser cannot provide it. */
  hint?: string | null;
  /** Runs synchronously inside the closing gesture (a fullscreen request needs it). */
  onClose?: () => void;
}

/**
 * Controller discovery stays live while the player reads the controls; the main menu (Continue, New game, Load game:
 * ui/menu.ts SaveMenuFlow.start) starts the game. Quitting a game reopens it.
 */
export class StartScreen implements MenuHost {
  readonly element = document.createElement('section');
  readonly menu: MenuPanel;
  active = true;
  private multiplayerReady = true;
  private status: HTMLElement;
  private touchLine: HTMLElement;
  private readonly onClose: () => void;
  private warning: HTMLElement | null = null;
  private menuReady = false;
  private afterWarning: (() => void) | null = null;

  constructor(input: Input, { hint = null, onClose = () => {} }: StartOptions = {}) {
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
      <p>Keyboard: WASD move · mouse look · Shift sprint · Space jump · E interact<br>F enter/exit · W/S drive/reverse · Space handbrake · R flip car · M map · Esc pause · F10 fullscreen${devToolsEnabled(location) ? '<br>P position readout · V hide/show character · L copy camera position · F4 / LB layers' : ''}</p>
      <p class="start-touch">Touch: left thumb moves and drives · drag right to look · pinch to zoom · buttons bottom right</p>
      <p class="start-fullscreen" hidden></p>
      <p class="start-hint">Loading the world…</p>
      <p class="start-credits">Map data, lidar and aerial imagery © City of Turku, CC BY 4.0 · Streets © OpenStreetMap contributors, ODbL · Scooter model by MiniPoly, CC BY 3.0</p>
    </div>`;
    const actions: [Action, string][] = [['moveY', 'Move / steer'], ['lookX', 'Look'], ['sprint', 'Sprint'],
      ['jump', 'Jump'], ['mission', 'Interact'], ['interact', 'Enter / exit car'], ['throttle', 'Accelerate'], ['brake', 'Brake / reverse'],
      ['handbrake', 'Handbrake'], ['vehicleReset', 'Flip car (also nearby on foot)'], ['cameraReset', 'Reset camera'], ['map', 'Map'], ['pause', 'Pause menu'], ['positionHud', 'Position readout'], ['photoCharacter', 'Hide / show character'], ['layerPanel', 'Layer panel']];
    for (const [action, label] of actions) {
      if (['positionHud', 'photoCharacter', 'layerPanel'].includes(action) && !devToolsEnabled(location)) continue;
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = glyph(action, input.bindings, true); dd.textContent = label;
      this.element.querySelector('dl')!.append(dt, dd);
    }
    const fullscreen = this.element.querySelector<HTMLElement>('.start-fullscreen')!;
    fullscreen.textContent = hint;
    fullscreen.hidden = !hint;
    // Acknowledge the unsupported browser before any start-menu choice can launch the game.
    // The warning is shown once the start screen is ready, above the compact loading dock.
    if (/(?:Firefox|FxiOS)\/\d+/i.test(navigator.userAgent)) {
      const warning = this.warning = document.createElement('section');
      warning.id = 'firefox-warning';
      warning.hidden = true;
      warning.setAttribute('role', 'alertdialog');
      warning.setAttribute('aria-modal', 'true');
      warning.setAttribute('aria-labelledby', 'firefox-warning-title');
      warning.setAttribute('aria-describedby', 'firefox-warning-copy');
      warning.innerHTML = `<div class="firefox-warning-card">
        <p class="eyebrow">TURKU3D · OFFICIAL GETAWAY ADVISORY</p>
        <h2 id="firefox-warning-title">Firefox detected. Your getaway car is a slideshow.</h2>
        <p id="firefox-warning-copy">Firefox is <strong>not supported</strong>. Even the cops can outrun those frame times.
          For the real Turku3D experience, please use <strong>Google Chrome</strong>.</p>
        <div class="firefox-warning-actions">
          <a href="https://www.google.com/chrome/" target="_blank" rel="noopener noreferrer">Get Chrome</a>
          <button type="button">I understand · let me suffer</button>
        </div>
      </div>`;
      warning.querySelector('button')!.addEventListener('click', () => this.dismissWarning());
      this.element.append(warning);
    }
    this.status = this.element.querySelector('#gamepad-status')!;
    this.touchLine = this.element.querySelector('.start-touch')!;
    this.menu = new MenuPanel(input);
    this.menu.setEnabled(false);
    this.status.after(this.menu.element);
    document.body.append(this.element);
  }
  show(page: MenuPage) {
    this.menu.show(this.multiplayerReady ? page : { ...page, items: page.items.map(item => item.id === 'multiplayer'
      ? { ...item, enabled: false, detail: 'Connection unavailable · reload to retry' } : item) });
  }
  /** Delay automatic saved-game resumes until Firefox's warning is acknowledged. */
  afterBrowserWarning(action: () => void) {
    if (this.warning) this.afterWarning = action;
    else action();
  }

  private dismissWarning() {
    if (!this.warning) return;
    this.warning.hidden = true;
    this.warning = null;
    this.element.classList.remove('browser-warning-open');
    this.menu.setEnabled(this.menuReady);
    const action = this.afterWarning;
    this.afterWarning = null;
    action?.();
  }

  /** The world is loaded: the menu accepts picks unless Firefox's warning is pending. */
  ready(multiplayerReady = true) {
    this.multiplayerReady = multiplayerReady;
    if (this.menu.page) this.show(this.menu.page);
    this.menuReady = true;
    this.menu.setEnabled(!this.warning);
    if (this.warning) {
      this.warning.hidden = false;
      this.element.classList.add('browser-warning-open');
      this.warning.querySelector('button')?.focus();
    }
    this.element.querySelector('.start-hint')!.textContent = multiplayerReady
      ? 'Press Ⓐ or select an option.' : 'Multiplayer unavailable. Choose solo play or reload to reconnect.';
  }
  update(input: Input) {
    this.status.textContent = input.gamepadName ? `${input.gamepadName} connected`
      : input.source === 'touch' ? 'Touch controls ready.' : 'No controller detected — connect it and press a button.';
    this.touchLine.hidden = !input.touchDevice;
    if (this.warning) {
      // Gamepad A / keyboard Enter can acknowledge too; never select a menu row in the same frame.
      if (input.consume('menuSelect')) this.dismissWarning();
      return;
    }
    this.menu.handle(input);
  }
  /** Leaves for the game; call from the pick that starts it (still inside the gesture). */
  close() {
    if (!this.active || this.warning) return;
    this.active = false;
    this.element.remove();
    this.onClose();
  }
  /** Back from a game: the menu `page` over the world. */
  reopen(page: MenuPage) {
    this.active = true;
    this.show(page);
    document.body.append(this.element);
  }
}
