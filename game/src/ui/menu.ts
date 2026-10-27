// Game menus: the list/option component every menu uses (shop, start screen, pause), a titled menu page, the pause
// overlay, and the save/load/new-game flow over core/save.ts. Navigation is the menu input actions: D-pad / left
// stick, A selects, B goes back; arrows or W/S, Enter or E, Escape; a tap or click on a row selects and chooses it.
import type { Input } from '../input';
import { MANUAL_SLOTS, SLOT_IDS, SLOT_NAMES, formatDetails, type SaveGame, type SaveStore, type SlotId } from '../core/save';

interface MenuRow { id: string; enabled: boolean }

/**
 * A vertical list of rows with one selected row. `fill` builds each row's content; a pick (menu select, or a pointer
 * release on a row) calls `onPick` synchronously, so a tap is still a user gesture there (fullscreen requests).
 * Disabled rows are picked too: the owner decides what a disabled pick means.
 */
export class MenuList<Row extends MenuRow = MenuRow> {
  readonly element = document.createElement('ol');
  selected = 0;
  private rows: readonly Row[] = [];

  constructor(private readonly prefix: string, private readonly onPick: (row: Row) => void) {
    this.element.className = `${prefix}-options`;
    this.element.setAttribute('role', 'listbox');
  }

  /** Replaces the rows. `select` sets the selected index; otherwise it is kept (clamped). */
  set(rows: readonly Row[], fill: (row: Row, li: HTMLLIElement) => void, select?: number) {
    this.rows = rows;
    this.selected = Math.max(0, Math.min(select ?? this.selected, rows.length - 1));
    this.element.replaceChildren(...rows.map((row, i) => {
      const li = document.createElement('li');
      li.className = `${this.prefix}-option`;
      li.dataset.option = row.id;
      li.setAttribute('role', 'option');
      li.classList.toggle('disabled', !row.enabled);
      li.setAttribute('aria-disabled', String(!row.enabled));
      fill(row, li);
      // pointerup serves mouse clicks and touch taps alike; a touch that began on a thumb button stays captured there.
      li.addEventListener('pointerup', () => { this.selected = i; this.highlight(); this.onPick(row); });
      return li;
    }));
    this.highlight();
  }

  /** Moves the selection and picks on menu select. Returns whether menu back was pressed this frame. */
  handle(input: Input): boolean {
    const n = this.rows.length;
    if (n && input.consume('menuUp')) this.selected = (this.selected - 1 + n) % n;
    if (n && input.consume('menuDown')) this.selected = (this.selected + 1) % n;
    this.highlight();
    const back = input.consume('menuBack');
    if (input.consume('menuSelect') && !back && n) this.onPick(this.rows[this.selected]);
    return back;
  }

  private highlight() {
    this.element.querySelectorAll('li').forEach((li, i) => {
      li.classList.toggle('selected', i === this.selected);
      li.setAttribute('aria-selected', String(i === this.selected));
    });
  }
}

export interface MenuItem extends MenuRow {
  label: string;
  detail: string | null;
  /** Runs on a pick of an enabled item. */
  run: () => void;
}

export interface MenuPage {
  /** Stable page id (`data-page`), e.g. `pause`, `save`, `load`, `confirm-new`. */
  id: string;
  title: string;
  /** A status or question line under the title, or null. */
  text: string | null;
  items: MenuItem[];
  /** Menu back on this page, or null where back does nothing. */
  back: (() => void) | null;
}

/** Where a flow shows its pages. */
export interface MenuHost { show(page: MenuPage): void }

/** One menu page at a time: title, status line, rows and the button hints. */
export class MenuPanel implements MenuHost {
  readonly element = document.createElement('div');
  page: MenuPage | null = null;
  /** While false (the world is loading) every row is greyed and picks are ignored. */
  private enabled = true;
  private source: Input['source'] | null = null;
  private readonly title = document.createElement('h2');
  private readonly text = document.createElement('p');
  private readonly hints = document.createElement('p');
  private readonly list = new MenuList<MenuItem>('menu', item => { if (this.enabled && item.enabled) item.run(); });

  constructor(private readonly input: Input) {
    this.element.className = 'menu-panel';
    this.title.className = 'menu-title';
    this.text.className = 'menu-text';
    this.hints.className = 'menu-hints';
    this.element.append(this.title, this.text, this.list.element, this.hints);
  }

  get selected() { return this.list.selected; }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (this.page) this.render(this.list.selected);
  }

  show(page: MenuPage) {
    this.page = page;
    this.render(Math.max(0, page.items.findIndex(i => i.enabled)));
  }

  /** Per frame while the menu is visible. Returns whether menu back was pressed. */
  handle(input: Input): boolean {
    if (this.page && input.source !== this.source) this.render(this.list.selected);
    const back = this.list.handle(input);
    if (back && this.enabled) this.page?.back?.();
    return back;
  }

  private render(select: number) {
    const page = this.page!;
    this.element.dataset.page = page.id;
    this.title.textContent = page.title;
    this.text.textContent = page.text ?? '';
    this.text.hidden = !page.text;
    const rows = page.items.map(item => ({ ...item, enabled: item.enabled && this.enabled }));
    this.list.set(rows, (item, li) => {
      const label = Object.assign(document.createElement('span'), { className: 'menu-label', textContent: item.label });
      li.append(label);
      if (item.detail) li.append(Object.assign(document.createElement('small'), { className: 'menu-detail', textContent: item.detail }));
    }, select);
    const i = this.input;
    this.source = i.source;
    this.hints.textContent = i.source === 'touch' ? `Tap an option${page.back ? ` · ${i.prompt('menuBack')} Back` : ''}`
      : `${i.prompt('menuUp')} ${i.prompt('menuDown')} choose · ${i.prompt('menuSelect')} select${page.back ? ` · ${i.prompt('menuBack')} back` : ''}`;
  }
}

/** The pause overlay (Menu / Esc / touch Pause): the world waits behind it. */
export class PauseMenu {
  readonly element = document.createElement('section');
  readonly panel: MenuPanel;

  constructor(parent: HTMLElement, input: Input) {
    this.element.id = 'pause-menu';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-modal', 'true');
    this.element.setAttribute('aria-label', 'Paused');
    this.element.hidden = true;
    this.panel = new MenuPanel(input);
    this.element.append(this.panel.element);
    parent.append(this.element);
  }

  get open() { return !this.element.hidden; }
  show(page: MenuPage) { this.element.hidden = false; this.panel.show(page); }
  close() { this.element.hidden = true; }
}

/** What the save menus do to the running game. */
export interface GameHooks {
  /** The running game as a save, labelled and timestamped. */
  capture(): SaveGame;
  /** Starts playing `game`, or a new game with null. Runs inside the pick (a user gesture). */
  play(game: SaveGame | null): void;
  /** Closes the pause menu. */
  resume(): void;
  /** Leaves the running game for the start screen. */
  quit(): void;
}

/** A slot row: its name and label, and the details line, or "Empty". */
function slotItem(store: SaveStore, id: SlotId, enabledEmpty: boolean, run: (game: SaveGame | null) => void): MenuItem {
  const game = store.get(id);
  return { id, label: game ? `${SLOT_NAMES[id]} · ${game.label}` : `${SLOT_NAMES[id]} · Empty`,
    detail: game ? formatDetails(game) : null, enabled: game !== null || enabledEmpty, run: () => run(game) };
}

/** The start-screen and pause-menu pages over a save store. */
export class SaveMenuFlow {
  constructor(private readonly host: MenuHost, private readonly store: SaveStore, private readonly game: GameHooks) {}

  /** Start screen: Continue (latest save, with its details), New game, Load game. */
  start(): MenuPage {
    const latest = this.store.latest();
    const items: MenuItem[] = [];
    if (latest) {
      items.push({ id: 'continue', label: 'Continue', detail: `${latest.game.label} · ${formatDetails(latest.game)}`,
        enabled: true, run: () => this.game.play(latest.game) });
    }
    items.push({ id: 'new', label: 'New game', detail: null, enabled: true,
      run: () => this.store.any ? this.host.show(this.confirmNew(() => this.start())) : this.game.play(null) });
    items.push({ id: 'load', label: 'Load game', detail: null, enabled: this.store.any,
      run: () => this.host.show(this.load(() => this.start())) });
    return { id: 'start', title: 'Main menu', text: null, items, back: null };
  }

  /** Pause menu root; `text` is a status line such as "Saved to Slot 2.". */
  pause(text: string | null = null): MenuPage {
    const root = () => this.pause();
    return { id: 'pause', title: 'Paused', text, back: () => this.game.resume(), items: [
      { id: 'resume', label: 'Resume', detail: null, enabled: true, run: () => this.game.resume() },
      { id: 'save', label: 'Save game', detail: null, enabled: true, run: () => this.host.show(this.save()) },
      { id: 'load', label: 'Load game', detail: null, enabled: this.store.any, run: () => this.host.show(this.load(root)) },
      { id: 'new', label: 'New game', detail: null, enabled: true, run: () => this.host.show(this.confirmNew(root)) },
      { id: 'quit', label: 'Quit to start screen', detail: null, enabled: true, run: () => this.game.quit() },
    ] };
  }

  /** Slot picker for saving; a filled slot asks before it is overwritten. */
  save(): MenuPage {
    const items = MANUAL_SLOTS.map(id => slotItem(this.store, id, true, existing => {
      if (existing) this.host.show(this.confirmOverwrite(id, existing));
      else this.saveTo(id);
    }));
    items.push({ id: 'back', label: 'Back', detail: null, enabled: true, run: () => this.host.show(this.pause()) });
    return { id: 'save', title: 'Save game', text: null, items, back: () => this.host.show(this.pause()) };
  }

  /** Slot list for loading: the autosave and the three slots, empty ones greyed out. */
  load(back: () => MenuPage): MenuPage {
    const items = SLOT_IDS.map(id => slotItem(this.store, id, false, game => { if (game) this.game.play(game); }));
    items.push({ id: 'back', label: 'Back', detail: null, enabled: true, run: () => this.host.show(back()) });
    return { id: 'load', title: 'Load game', text: null, items, back: () => this.host.show(back()) };
  }

  private confirmOverwrite(id: SlotId, existing: SaveGame): MenuPage {
    return { id: 'confirm-overwrite', title: 'Save game', text: `Overwrite ${SLOT_NAMES[id]} (${existing.label})?`,
      back: () => this.host.show(this.save()), items: [
        { id: 'yes', label: 'Overwrite', detail: null, enabled: true, run: () => this.saveTo(id) },
        { id: 'no', label: 'Cancel', detail: null, enabled: true, run: () => this.host.show(this.save()) },
      ] };
  }

  private confirmNew(back: () => MenuPage): MenuPage {
    return { id: 'confirm-new', title: 'New game', text: 'Start a new game? Progress since your last save is lost.',
      back: () => this.host.show(back()), items: [
        { id: 'yes', label: 'Start a new game', detail: null, enabled: true, run: () => this.game.play(null) },
        { id: 'no', label: 'Cancel', detail: null, enabled: true, run: () => this.host.show(back()) },
      ] };
  }

  private saveTo(id: SlotId) {
    this.store.write(id, this.game.capture());
    this.host.show(this.pause(`Saved to ${SLOT_NAMES[id]}.`));
  }
}
