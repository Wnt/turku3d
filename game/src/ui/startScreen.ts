import { CITY } from '../core/city';
import { devToolsEnabled } from '../core/localOnly';
import { FULLSCREEN_KEY, type Action, type Input } from '../input';
import { MenuPanel, type MenuHost, type MenuPage } from './menu';

/** The controller reference (both columns of #start-bindings); dev-only rows at the end. */
const PAD_ROWS: [Action, string][] = [['moveY', 'Move / steer'], ['lookX', 'Look'], ['sprint', 'Sprint'],
  ['jump', 'Jump'], ['mission', 'Interact'], ['interact', 'Enter / exit car'], ['throttle', 'Accelerate'], ['brake', 'Brake / reverse'],
  ['horn', 'Horn (hold)'], ['lights', 'Lights: off / driving / long beams'], ['handbrake', 'Handbrake'], ['vehicleReset', 'Flip car (also nearby on foot)'],
  ['cameraReset', 'Reset camera'], ['map', 'Map'], ['pause', 'Pause menu'], ['positionHud', 'Position readout'], ['photoCharacter', 'Hide / show character'],
  ['layerPanel', 'Layer panel']];
const DEV_ROWS = new Set<Action>(['positionHud', 'photoCharacter', 'layerPanel', 'copyPosition', 'timePanel']);
/** The keyboard reference, one line each; an entry of two actions shares one label ("W/S drive/reverse"). */
const KEY_LINES: [Action | [Action, Action], string][][] = [
  [['moveY', 'move'], ['lookX', 'look'], ['sprint', 'sprint'], ['jump', 'jump'], ['mission', 'interact']],
  [['interact', 'enter/exit'], [['throttle', 'brake'], 'drive/reverse'], ['handbrake', 'handbrake'], ['horn', 'horn (hold)'],
    ['lights', 'lights'], ['vehicleReset', 'flip car'], ['map', 'map'], ['pause', 'pause']],
  [['positionHud', 'position readout'], ['photoCharacter', 'hide/show character'], ['copyPosition', 'copy camera position'],
    ['layerPanel', 'layers'], ['timePanel', 'time of day']],
];

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
  private multiplayerReady: boolean | null = true;
  private page: MenuPage | null = null;
  private status: HTMLElement;
  private touchLine: HTMLElement;
  private readonly onClose: () => void;
  private warning: HTMLElement | null = null;
  private menuReady = false;
  private afterWarning: (() => void) | null = null;
  private readonly input: Input;
  private readonly bindingList: HTMLElement;
  private readonly keyLine: HTMLElement;
  private readonly hint: HTMLElement;
  /** input.promptRevision the references and the hint were drawn for. */
  private drawn = -1;
  /** The hint says "Loading the world…" until the first setMultiplayerReady. */
  private loaded = false;
  /** A hint that replaces the ready line (notice). */
  private notice: string | null = null;

  constructor(input: Input, { hint = null, onClose = () => {} }: StartOptions = {}) {
    this.onClose = onClose;
    this.element.id = 'start-screen';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-labelledby', 'start-title');
    this.element.innerHTML = `<div class="start-card">
      <p class="eyebrow">${CITY.title.toUpperCase()} · FREE ROAM</p>
      <h1 id="start-title">${CITY.spawn.label}</h1>
      <p>Explore on foot. Your car is parked nearby.</p>
      <p id="gamepad-status" role="status"></p>
      <dl id="start-bindings"></dl>
      <p class="start-keys"></p>
      <p class="start-touch">Touch: left thumb moves and drives · drag right to look · pinch to zoom · buttons bottom right</p>
      <p class="start-fullscreen" hidden></p>
      <p class="start-hint">Loading the world…</p>
      <p class="start-credits">The game world is derived from and modified from: map data, lidar and aerial imagery © City of Turku, <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a> · Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>, ODbL · Scooter model by MiniPoly, CC BY 3.0</p>
    </div>`;
    this.input = input;
    this.bindingList = this.element.querySelector('#start-bindings')!;
    this.keyLine = this.element.querySelector('.start-keys')!;
    this.hint = this.element.querySelector('.start-hint')!;
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
    this.drawPrompts();
    document.body.append(this.element);
  }

  /** The controller list in the button names of the pad in use (or seen last), the keyboard line, and the hint in
   *  the names of the device in use; redrawn when the device or the pad changes. */
  private drawPrompts() {
    const input = this.input;
    if (this.drawn === input.promptRevision) return;
    this.drawn = input.promptRevision;
    const dev = devToolsEnabled(location);
    this.bindingList.replaceChildren(...PAD_ROWS.filter(([action]) => dev || !DEV_ROWS.has(action)).flatMap(([action, label]) => [
      Object.assign(document.createElement('dt'), { textContent: input.prompt(action, input.padFamily) }),
      Object.assign(document.createElement('dd'), { textContent: label })]));
    const key = (action: Action) => input.prompt(action, 'keyboard');
    const lines = KEY_LINES.filter(line => dev || !line.every(([action]) => DEV_ROWS.has(action as Action)))
      .map(line => line.map(([actions, label]) => `${typeof actions === 'string' ? key(actions) : actions.map(key).join('/')} ${label}`).join(' · '));
    lines[1] += ` · ${FULLSCREEN_KEY} fullscreen`;
    this.keyLine.replaceChildren(`Keyboard: ${lines[0]}`, ...lines.slice(1).flatMap(line => [document.createElement('br'), line]));
    if (!this.loaded) return;
    this.hint.textContent = this.notice ?? (this.multiplayerReady === null ? 'Solo play is ready. Multiplayer is connecting…'
      : !this.multiplayerReady ? 'Multiplayer unavailable. Choose solo play or reload to reconnect.'
      : input.source === 'touch' ? 'Tap an option.' : `Press ${input.prompt('menuSelect')} or select an option.`);
  }

  /** `text` replaces the ready hint until the next setMultiplayerReady. */
  showNotice(text: string) {
    this.notice = text;
    this.drawn = -1;
    this.drawPrompts();
  }

  show(page: MenuPage) {
    this.page = page;
    this.menu.show(this.multiplayerReady ? page : { ...page, items: page.items.map(item => item.id === 'multiplayer'
      ? { ...item, enabled: false, detail: this.multiplayerReady === null ? 'Connecting… · solo play is ready' : 'Connection unavailable · reload to retry' } : item) });
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
  ready(multiplayerReady: boolean | null = true) {
    this.setMultiplayerReady(multiplayerReady);
    this.menuReady = true;
    this.menu.setEnabled(!this.warning);
    if (this.warning) {
      this.warning.hidden = false;
      this.element.classList.add('browser-warning-open');
      this.warning.querySelector('button')?.focus();
    }
  }
  setMultiplayerReady(multiplayerReady: boolean | null) {
    this.multiplayerReady = multiplayerReady;
    if (this.page) this.show(this.page);
    this.loaded = true;
    this.notice = null;
    this.drawn = -1;
    this.drawPrompts();
  }
  update(input: Input) {
    this.drawPrompts();
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
