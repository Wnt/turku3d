// Full-screen shop menu over the world: the shop's illustration, the shopkeeper's latest line and the options with
// prices. Greyed options show why they are not available. It renders a MissionSystem ShopView and turns the menu
// actions (D-pad / left stick, A, B; arrows or W/S, Enter or E, Escape; a tap or click on a row and the touch Back
// button) into the mission frame's choose / leave.
import type { Input, InputSource } from '../input';
import type { OptionId } from '../gameplay/content/shops';
import type { ShopOptionView, ShopView } from '../gameplay/missions/missions';
import { formatTimer } from '../gameplay/minimap';

interface MenuCommand { choose: OptionId | null; leave: boolean }

/** Price column text: what the player pays, `+` when the player is paid, empty without a price. */
export function formatPrice(price: number | null): string {
  if (price === null) return '';
  return price < 0 ? `+${-price} €` : `${price} €`;
}

/** The pizzeria's order quote, or the option's static detail line. */
export function optionDetail(option: ShopOptionView): string | null {
  if (option.quote) {
    const { address, metres, par } = option.quote;
    return `${address} · ${Math.round(metres)} m · par ${formatTimer(par)}`;
  }
  return option.detail;
}

export class ShopMenu {
  readonly element = document.createElement('section');
  selected = 0;
  private view: ShopView | null = null;
  private source: InputSource | null = null;
  private clicked: OptionId | null = null;
  private readonly list = document.createElement('ol');
  private readonly line = document.createElement('p');
  private readonly title = document.createElement('h2');
  private readonly speaker = document.createElement('p');
  private readonly status = document.createElement('p');
  private readonly hints = document.createElement('p');

  constructor(parent: HTMLElement, private readonly assets = `${import.meta.env.BASE_URL}assets/ui/`) {
    this.element.id = 'shop-menu';
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-modal', 'true');
    this.element.hidden = true;
    const panel = document.createElement('div');
    panel.className = 'shop-panel';
    this.speaker.className = 'shop-speaker';
    this.line.className = 'shop-line';
    this.status.className = 'shop-status';
    this.hints.className = 'shop-hints';
    this.list.className = 'shop-options';
    this.title.id = 'shop-title';
    this.element.setAttribute('aria-labelledby', this.title.id);
    panel.append(this.title, this.status, this.speaker, this.line, this.list, this.hints);
    this.element.append(panel);
    parent.append(this.element);
  }

  get open() { return this.view !== null; }

  /** Shows `view` (null hides the menu). Cheap when nothing changed. */
  render(view: ShopView | null, input: Input) {
    const reopened = view !== null && (this.view === null || this.view.id !== view.id);
    if (view === this.view && input.source === this.source) return;
    this.view = view;
    this.source = input.source;
    this.element.hidden = view === null;
    if (!view) return;
    if (reopened) this.selected = Math.max(0, view.options.findIndex(o => o.enabled));
    this.selected = Math.min(this.selected, view.options.length - 1);
    this.element.dataset.shop = view.id;
    this.element.style.setProperty('--shop-art', `url("${this.assets}${view.background}.webp")`);
    this.title.textContent = view.title;
    this.status.textContent = `Cash ${view.money} € · Courier permit: ${view.permit === 1 ? '1 delivery' : `${view.permit} deliveries`} left`;
    this.speaker.textContent = view.speaker;
    this.line.textContent = view.line;
    this.list.replaceChildren(...view.options.map((option, i) => this.row(option, i)));
    this.hints.textContent = input.source === 'touch' ? `Tap an option to select · ${input.prompt('menuBack')} Back to leave`
      : `${input.prompt('menuUp')} ${input.prompt('menuDown')} choose · ${input.prompt('menuSelect')} select · ${input.prompt('menuBack')} leave`;
    this.highlight();
  }

  /** Moves the selection and returns this frame's menu command. Call only while the menu is open. */
  handle(input: Input): MenuCommand {
    const view = this.view;
    if (!view) return { choose: null, leave: false };
    const n = view.options.length;
    if (input.consume('menuUp')) this.selected = (this.selected - 1 + n) % n;
    if (input.consume('menuDown')) this.selected = (this.selected + 1) % n;
    this.highlight();
    const leave = input.consume('menuBack');
    const choose = this.clicked ?? (input.consume('menuSelect') ? view.options[this.selected].id : null);
    this.clicked = null;
    return { choose: leave ? null : choose, leave };
  }

  private row(option: ShopOptionView, index: number) {
    const li = document.createElement('li');
    li.className = 'shop-option';
    li.dataset.option = option.id;
    li.classList.toggle('disabled', !option.enabled);
    li.setAttribute('aria-disabled', String(!option.enabled));
    const icon = document.createElement('span');
    icon.className = 'shop-icon';
    if (option.icon) icon.style.backgroundImage = `url("${this.assets}${option.icon}.webp")`;
    else icon.classList.add('empty');
    const text = document.createElement('span');
    text.className = 'shop-text';
    const label = document.createElement('span');
    label.className = 'shop-label';
    label.textContent = option.label;
    text.append(label);
    const detail = option.reason ?? optionDetail(option);
    if (detail) {
      const small = document.createElement('small');
      small.className = option.reason ? 'shop-reason' : 'shop-detail';
      small.textContent = detail;
      text.append(small);
    }
    const price = document.createElement('span');
    price.className = 'shop-price';
    price.textContent = formatPrice(option.price);
    price.classList.toggle('income', (option.price ?? 0) < 0);
    li.append(icon, text, price);
    // pointerup serves mouse clicks and touch taps alike; a touch that began on a thumb button stays captured there.
    li.addEventListener('pointerup', () => { this.selected = index; this.clicked = option.id; this.highlight(); });
    return li;
  }

  private highlight() {
    this.list.querySelectorAll('li').forEach((li, i) => {
      li.classList.toggle('selected', i === this.selected);
      li.setAttribute('aria-selected', String(i === this.selected));
    });
  }
}
