// Deterministic mission state machine: courier permit, shops, pizza pickup, timed delivery, pay and tips
// (docs/design/story.md, "Mission state machine" and "Shops"). It is driven only by update(dt, frame), so tests
// replay it exactly. Messages are dialogue line ids resolved to their exact text (content/dialogue.json).
import type { HudStateListener, MissionHudSource, MissionHudState, RouteTarget, Router, WorldPoint } from '../contract';
import {
  BASE_PAY, KIDNEY_PAYOUT, KIDNEY_RUN_FACTOR, PERMIT_DELIVERIES, PERMIT_MAX, PERMIT_PRICE, PERMIT_WARN_AT,
  PLATES_PRICE, SLICE_PRICE, SLICE_SECONDS, START_MONEY, VEHICLE_PRICE, WARNING_PRICE, deliveryDeadline, parSeconds,
  sellBackPrice, tip, type VehicleOwnership,
} from '../economy';
import { DIALOGUE, lineText } from '../content/dialogue';
import { DELIVERIES, type Delivery } from '../content/deliveries';
import { DEALER, MARKET, PIZZERIA } from '../content/locations';
import { SHOPS, optionDetail, optionLabel, type OptionId, type Shop, type ShopId } from '../content/shops';

type Phase = 'idle' | 'permit_needed' | 'at_pizzeria' | 'carrying' | 'delivered' | 'failed';
type BoughtVehicle = Exclude<VehicleOwnership, 'none'>;

/** Horizontal distance (m) within which the interaction button acts on a target. */
export const INTERACT_RADIUS = 3;
/** An open shop closes when the player is moved further than this from its zone centre. */
const SHOP_LEAVE_RADIUS = 6;
/** The player's own car (physics vehicle id); it also stands in for the bicycle. */
export const OWN_VEHICLE_ID = 'player';
export const SAVE_KEY = 'turku3d.missions';

interface MissionSave {
  version: 2;
  phase: Phase;
  money: number;
  /** Delivery orders left on the rented courier permit; taking an order uses one. */
  permit: number;
  plates: boolean;
  kidney: boolean;
  /** The permit expiry warning was bought back. */
  warning: boolean;
  vehicle: VehicleOwnership;
  /** Index of the current or next delivery; it grows without bound and wraps through DELIVERIES. */
  delivery: number;
  /** Seconds since pickup and the par time of the delivery being carried. */
  elapsed: number;
  par: number;
}

/** The save format of main 2673ec0 (one-off licence instead of the permit counter). */
interface MissionSaveV1 {
  version: 1;
  phase: 'idle' | 'licence_needed' | 'at_pizzeria' | 'carrying' | 'delivered' | 'failed';
  money: number;
  licence: boolean;
  vehicle: VehicleOwnership;
  delivery: number;
  elapsed: number;
  par: number;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface MissionFrame {
  /** Player feet (on foot) or vehicle position (driving). */
  position: WorldPoint;
  /** The interaction button was pressed this frame (opens a shop or hands over a pizza). */
  interact: boolean;
  /** Selects the button glyph in prompts. */
  input: 'keyboard' | 'gamepad';
  /** Physics id of the vehicle the player drives, or null on foot. */
  vehicleId: string | null;
  /** The menu option the player selected this frame in the open shop. */
  choose?: OptionId | null;
  /** The player closed the open shop this frame. */
  leave?: boolean;
}

interface MissionOptions {
  router: Router;
  storage: KeyValueStore | null;
}

/** What the pizzeria quotes before an order is accepted. */
export interface OrderQuote { address: string; metres: number; par: number }

export interface ShopOptionView {
  id: OptionId;
  label: string;
  detail: string | null;
  /** Euros the player pays (negative: the player is paid), or null when the option has no price. */
  price: number | null;
  icon: string | null;
  enabled: boolean;
  /** Why the option is greyed out, or null. */
  reason: string | null;
  quote: OrderQuote | null;
}

/** Everything the shop menu shows; texts are exact dialogue line texts. */
export interface ShopView {
  id: ShopId;
  title: string;
  speaker: string;
  /** The shopkeeper's latest line in this visit: the greeting, then each reply. */
  line: string;
  background: string;
  money: number;
  /** Delivery orders left on the courier permit. */
  permit: number;
  options: ShopOptionView[];
}

interface Action {
  target: RouteTarget;
  /** Prompt base id; the input device's button glyph is appended (`_pad` / `_key`). */
  prompt: string;
  run: () => void;
}

interface OptionState { price: number | null; reason: string | null; quote?: OrderQuote }

const VALID_PHASES: readonly Phase[] = ['idle', 'permit_needed', 'at_pizzeria', 'carrying', 'delivered', 'failed'];
const VALID_VEHICLES: readonly VehicleOwnership[] = ['none', 'bicycle', 'car'];
const SHOP_SPEAKER: Record<ShopId, string> = { market: 'market', pizzeria: 'pizzeria', dealer: 'dealer' };
const SHOP_PROMPT: Record<ShopId, string> = {
  market: 'prompt_enter_market', pizzeria: 'prompt_enter_pizzeria', dealer: 'prompt_enter_dealer',
};
const SHOP_TITLE: Record<ShopId, string> = { market: 'zone_market', pizzeria: 'zone_pizzeria', dealer: 'zone_dealer' };
const NO_MONEY_LINE: Record<ShopId, string> = {
  market: 'market_no_money', pizzeria: 'pizzeria_no_money', dealer: 'dealer_no_money',
};

/** Seconds a dialogue line stays on the message line: long enough to read it or hear it spoken. */
export function lineSeconds(text: string): number {
  return Math.min(7, Math.max(2.5, 1.2 + text.length * 0.06));
}

const distance = (a: WorldPoint, b: WorldPoint) => Math.hypot(a.x - b.x, a.z - b.z);

function freshSave(): MissionSave {
  return { version: 2, phase: 'idle', money: START_MONEY, permit: 0, plates: false, kidney: false, warning: false,
    vehicle: 'none', delivery: 0, elapsed: 0, par: 0 };
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const bool = (v: unknown): v is boolean => typeof v === 'boolean';

/** A v1 save (main 2673ec0): an owned licence becomes one fresh permit rental. */
function migrateV1(s: Partial<MissionSaveV1>): MissionSave | null {
  const phases = ['idle', 'licence_needed', 'at_pizzeria', 'carrying', 'delivered', 'failed'];
  if (!phases.includes(s.phase as string) || !num(s.money) || !bool(s.licence)
    || !VALID_VEHICLES.includes(s.vehicle as VehicleOwnership) || !num(s.delivery) || !num(s.elapsed) || !num(s.par)) {
    return null;
  }
  const phase: Phase = s.phase === 'licence_needed' ? 'permit_needed' : s.phase as Phase;
  return { version: 2, phase, money: s.money, permit: s.licence ? PERMIT_DELIVERIES : 0, plates: false, kidney: false,
    warning: false, vehicle: s.vehicle as VehicleOwnership, delivery: s.delivery, elapsed: s.elapsed, par: s.par };
}

function validV2(s: Partial<MissionSave>): MissionSave | null {
  if (!VALID_PHASES.includes(s.phase as Phase) || !num(s.money) || !num(s.permit) || !bool(s.plates) || !bool(s.kidney)
    || !bool(s.warning) || !VALID_VEHICLES.includes(s.vehicle as VehicleOwnership) || !num(s.delivery)
    || !num(s.elapsed) || !num(s.par)) {
    return null;
  }
  return s as MissionSave;
}

function parseSave(raw: string | null): MissionSave {
  if (!raw) return freshSave();
  try {
    const parsed = JSON.parse(raw) as { version?: unknown };
    const save = parsed?.version === 1 ? migrateV1(parsed as Partial<MissionSaveV1>)
      : parsed?.version === 2 ? validV2(parsed as Partial<MissionSave>) : null;
    if (!save) return freshSave();
    // A finished delivery's result line is not replayed after a reload: the next step is the pizzeria.
    if (save.phase === 'delivered' || save.phase === 'failed' || (save.phase === 'carrying' && save.par <= 0)) {
      save.phase = save.permit > 0 ? 'at_pizzeria' : 'permit_needed';
    }
    return { ...save, money: Math.round(save.money), permit: Math.floor(save.permit), delivery: Math.floor(save.delivery) };
  } catch {
    return freshSave();
  }
}

/** Whether the store holds a started game; the start screen shows the intro only for a new game. */
export function hasSavedGame(storage: KeyValueStore | null): boolean {
  return parseSave(storage?.getItem(SAVE_KEY) ?? null).phase !== 'idle';
}

export class MissionSystem implements MissionHudSource {
  private s: MissionSave;
  private queue: { id: string; left: number }[] = [];
  private lastResult: 'delivered' | 'failed' | null = null;
  private frame: MissionFrame | null = null;
  private listeners = new Set<HudStateListener>();
  private hud: MissionHudState;
  private savedSecond = -1;
  /** Seconds of slice energy left (not saved: a reload starts without it). */
  private energy = 0;
  private open: { shop: Shop; line: string } | null = null;
  private view: ShopView | null = null;
  private quoteCache: { key: string; quote: OrderQuote } | null = null;

  constructor(private readonly options: MissionOptions) {
    this.s = parseSave(options.storage?.getItem(SAVE_KEY) ?? null);
    this.hud = this.buildState();
  }

  get phase(): Phase { return this.s.phase; }
  get money(): number { return this.s.money; }
  get permit(): number { return this.s.permit; }
  get vehicle(): VehicleOwnership { return this.s.vehicle; }
  get plates(): boolean { return this.s.plates; }
  get kidney(): boolean { return this.s.kidney; }
  get currentDelivery(): Delivery { return DELIVERIES[this.s.delivery % DELIVERIES.length]; }
  /** Multiplier on the player's run and sprint speed. */
  get runFactor(): number { return this.s.kidney && this.energy <= 0 ? KIDNEY_RUN_FACTOR : 1; }
  /** The open shop menu, or null. The same object is returned until something in it changes. */
  get shop(): ShopView | null { return this.view; }
  snapshot() {
    return { ...this.s, address: this.currentDelivery.address, dialogue: this.queue.map(q => q.id), energy: this.energy,
      runFactor: this.runFactor, shop: this.view, hud: this.hud };
  }

  subscribe(listener: HudStateListener): () => void {
    this.listeners.add(listener);
    listener(this.hud);
    return () => { this.listeners.delete(listener); };
  }

  /** Leaves idle once the player starts playing; a continued game keeps its saved phase. */
  begin() {
    if (this.s.phase !== 'idle') return;
    this.s.phase = this.restPhase();
    this.persist();
    this.emit();
  }

  /** Whether the player may enter this vehicle now. While carrying, only the player's own vehicle counts,
   *  unless forged plates make every car count. */
  mayDrive(vehicleId: string): boolean {
    if (this.s.phase !== 'carrying' || this.mayDeliverWith(vehicleId)) return true;
    this.say('notice_own_vehicle_only');
    this.emit();
    return false;
  }

  /** Advances timers and dialogue by dt seconds (0 while paused), handles the interaction button and the menu. */
  update(dt: number, frame: MissionFrame): { bought: BoughtVehicle | null; opened: boolean } {
    this.frame = frame;
    let bought: BoughtVehicle | null = null;
    const wasOpen = this.open !== null;
    this.advanceDialogue(dt);
    this.energy = Math.max(0, this.energy - dt);
    if ((this.s.phase === 'delivered' || this.s.phase === 'failed') && this.queue.length === 0) {
      this.s.phase = this.restPhase();
      this.persist();
    }
    if (this.s.phase === 'carrying' && dt > 0) {
      this.s.elapsed += dt;
      if (this.s.elapsed > deliveryDeadline(this.s.par)) this.fail();
      else if (Math.floor(this.s.elapsed) !== this.savedSecond) this.persist();
    }
    if (this.open) {
      if (frame.leave || distance(frame.position, this.open.shop.target.point) > SHOP_LEAVE_RADIUS) this.open = null;
      else if (frame.choose) {
        const before = this.s.vehicle;
        this.choose(frame.choose);
        if (this.s.vehicle !== before && this.s.vehicle !== 'none') bought = this.s.vehicle;
      }
    } else if (frame.interact && this.s.phase !== 'idle') {
      this.action(frame)?.run();
    }
    this.view = this.buildView();
    this.emit();
    return { bought, opened: !wasOpen && this.open !== null };
  }

  // ----------------------------------------------------------------------------------------------- actions
  private owns(vehicleId: string) { return vehicleId === OWN_VEHICLE_ID && this.s.vehicle !== 'none'; }
  private mayDeliverWith(vehicleId: string | null) { return vehicleId === null || this.owns(vehicleId) || this.s.plates; }
  private restPhase(): Phase { return this.s.permit > 0 ? 'at_pizzeria' : 'permit_needed'; }

  private dropoffTarget(): RouteTarget {
    const d = this.currentDelivery;
    return { id: d.id, kind: 'dropoff', label: d.address, point: d.point };
  }

  /** The action of the nearest target in range: enter a shop, or hand over the pizza at the drop-off. */
  private action(frame: MissionFrame): Action | null {
    const candidates: Action[] = Object.values(SHOPS).map(shop => ({
      target: shop.target, prompt: SHOP_PROMPT[shop.id], run: () => this.enter(shop),
    }));
    if (this.s.phase === 'carrying') {
      candidates.push({ target: this.dropoffTarget(), prompt: 'prompt_hand_over', run: () => this.handOver() });
    }
    let best: Action | null = null, bestDistance = INTERACT_RADIUS;
    for (const c of candidates) {
      const d = distance(frame.position, c.target.point);
      if (d <= bestDistance) { best = c; bestDistance = d; }
    }
    return best;
  }

  private enter(shop: Shop) {
    const line = this.greeting(shop.id);
    this.open = { shop, line };
    this.say(line);
  }

  private greeting(id: ShopId): string {
    if (id === 'market') return this.s.permit === 0 && this.s.delivery > 0 ? 'market_greet_expired' : 'market_greet';
    if (id === 'dealer') return this.s.permit > 0 || this.s.delivery > 0 ? 'dealer_greet' : 'dealer_no_permit';
    if (this.s.phase === 'carrying') return 'pizzeria_carrying';
    if (this.s.permit === 0) return 'pizzeria_no_permit';
    if (this.lastResult === 'failed') return 'pizzeria_after_fail';
    if (this.lastResult === 'delivered') return 'pizzeria_back';
    return this.s.delivery === 0 ? 'pizzeria_first' : 'pizzeria_greet';
  }

  /** Price and availability of an option right now. */
  private optionState(id: OptionId): OptionState {
    const s = this.s;
    const money = (price: number): OptionState => ({ price, reason: s.money < price ? 'reason_no_money' : null });
    const dealerOpen = s.permit > 0 || s.delivery > 0;
    switch (id) {
      case 'rent_permit':
        return s.permit + PERMIT_DELIVERIES > PERMIT_MAX ? { price: PERMIT_PRICE, reason: 'reason_permit_stock' } : money(PERMIT_PRICE);
      case 'forged_plates': return s.plates ? { price: PLATES_PRICE, reason: 'reason_plates_owned' } : money(PLATES_PRICE);
      case 'sell_kidney': return { price: -KIDNEY_PAYOUT, reason: s.kidney ? 'reason_kidney_sold' : null };
      case 'expiry_warning': return s.warning ? { price: WARNING_PRICE, reason: 'reason_warning_owned' } : money(WARNING_PRICE);
      case 'take_order': {
        const quote = this.quote();
        const reason = s.phase === 'carrying' ? 'reason_carrying' : s.permit === 0 ? 'reason_no_permit'
          : !this.mayDeliverWith(this.frame?.vehicleId ?? null) ? 'reason_own_vehicle_only' : null;
        return { price: null, reason, quote };
      }
      case 'slice': return money(SLICE_PRICE);
      case 'tip_rules': return { price: null, reason: null };
      case 'buy_bicycle':
      case 'buy_car': {
        const price = VEHICLE_PRICE[id === 'buy_car' ? 'car' : 'bicycle'];
        if (!dealerOpen) return { price, reason: 'reason_no_permit' };
        return s.vehicle !== 'none' ? { price, reason: 'reason_vehicle_owned' } : money(price);
      }
      case 'sell_vehicle':
        if (s.vehicle === 'none') return { price: null, reason: dealerOpen ? 'reason_no_vehicle' : 'reason_no_permit' };
        return { price: -sellBackPrice(s.vehicle), reason: dealerOpen ? null : 'reason_no_permit' };
      case 'leave': return { price: null, reason: null };
    }
  }

  private choose(id: OptionId) {
    const shop = this.open!.shop;
    if (!shop.options.some(o => o.id === id)) return;
    if (id === 'leave') { this.open = null; return; }
    const { price, reason } = this.optionState(id);
    if (reason) {
      if (reason === 'reason_no_money') this.reply(NO_MONEY_LINE[shop.id]);
      return;
    }
    if (price !== null) this.s.money -= price;
    switch (id) {
      case 'rent_permit':
        this.s.permit += PERMIT_DELIVERIES;
        if (this.s.phase === 'permit_needed') this.s.phase = 'at_pizzeria';
        this.reply('market_sold');
        break;
      case 'forged_plates': this.s.plates = true; this.reply('market_sold_plates'); break;
      case 'sell_kidney': this.s.kidney = true; this.reply('market_sold_kidney'); break;
      case 'expiry_warning': this.s.warning = true; this.reply('market_sold_warning'); break;
      case 'take_order': this.pickUp(); this.open = null; break;
      case 'slice': this.energy = SLICE_SECONDS; this.reply('pizzeria_slice'); break;
      case 'tip_rules': this.reply('pizzeria_tip_rules'); break;
      case 'buy_bicycle':
      case 'buy_car':
        this.s.vehicle = id === 'buy_car' ? 'car' : 'bicycle';
        this.say(id === 'buy_car' ? 'dealer_sold_car' : 'dealer_sold_bicycle');
        this.open = null; // the vehicle waits at the kerb
        break;
      case 'sell_vehicle': this.s.vehicle = 'none'; this.reply('dealer_bought_back'); break;
    }
    this.persist();
  }

  /** The shopkeeper answers inside the open menu; the line is also the HUD message, so it is voiced. */
  private reply(id: string) {
    if (this.open) this.open.line = id;
    this.say(id);
  }

  private quote(): OrderQuote {
    const d = this.currentDelivery;
    const vehicle = this.parVehicle();
    const key = `${this.s.delivery}|${vehicle}`;
    if (this.quoteCache?.key !== key) {
      const path = this.options.router.route(PIZZERIA.point, d.point);
      const metres = path ? this.options.router.distance(path) : distance(PIZZERIA.point, d.point);
      this.quoteCache = { key, quote: { address: d.address, metres, par: parSeconds(metres, vehicle) } };
    }
    return this.quoteCache.quote;
  }

  /** Par speed class: the owned vehicle, or a car when a borrowed car is used with forged plates. */
  private parVehicle(): VehicleOwnership {
    const id = this.frame?.vehicleId ?? null;
    return id !== null && !this.owns(id) && this.s.plates ? 'car' : this.s.vehicle;
  }

  private pickUp() {
    const d = this.currentDelivery;
    this.s.par = this.quote().par;
    this.s.elapsed = 0;
    this.s.permit -= 1;
    this.s.phase = 'carrying';
    this.say(d.handover);
  }

  private handOver() {
    const d = this.currentDelivery;
    const t = tip(this.s.elapsed, this.s.par);
    this.s.money += BASE_PAY + t;
    this.s.phase = 'delivered';
    this.s.delivery += 1;
    this.s.elapsed = this.s.par = 0;
    this.lastResult = 'delivered';
    this.say(t >= 5 ? d.customer.fast : t >= 1 ? d.customer.ok : d.customer.slow, `paid_tip_${t}`, ...this.permitWarning());
    this.persist();
  }

  private fail() {
    this.s.phase = 'failed';
    this.s.delivery += 1;
    this.s.elapsed = this.s.par = 0;
    this.lastResult = 'failed';
    this.say('delivery_failed', ...this.permitWarning());
    this.persist();
  }

  private permitWarning(): string[] {
    return this.s.warning && this.s.permit <= PERMIT_WARN_AT ? [`permit_left_${this.s.permit}`] : [];
  }

  // ---------------------------------------------------------------------------------------- dialogue, state
  /** Replaces whatever is being said with these lines, shown one after another. */
  private say(...ids: string[]) {
    this.queue = ids.map(id => ({ id, left: lineSeconds(lineText(id)) }));
  }

  private advanceDialogue(dt: number) {
    let left = dt;
    while (this.queue.length && left > 0) {
      const head = this.queue[0];
      const used = Math.min(left, head.left);
      head.left -= used;
      left -= used;
      if (head.left <= 0) this.queue.shift();
    }
  }

  private messageId(): string | null {
    if (this.queue.length) return this.queue[0].id;
    if (this.open) return null; // the menu shows the shopkeeper; no prompt behind it
    const action = this.frame ? this.action(this.frame) : null;
    if (action && this.s.phase !== 'idle') return `${action.prompt}_${this.frame!.input === 'gamepad' ? 'pad' : 'key'}`;
    switch (this.s.phase) {
      case 'idle': return null;
      case 'permit_needed': return 'hint_permit';
      case 'carrying': return this.currentDelivery.hint;
      default: return 'hint_pizzeria';
    }
  }

  private buildView(): ShopView | null {
    if (!this.open) return null;
    const { shop, line } = this.open;
    const options = shop.options.map(o => {
      const state = this.optionState(o.id);
      const detail = optionDetail(o.id);
      return { id: o.id, label: lineText(optionLabel(o.id)), detail: detail ? lineText(detail) : null, price: state.price,
        icon: o.icon, enabled: state.reason === null, reason: state.reason ? lineText(state.reason) : null,
        quote: state.quote ?? null };
    });
    const next: ShopView = { id: shop.id, title: lineText(SHOP_TITLE[shop.id]),
      speaker: DIALOGUE.speakers[SHOP_SPEAKER[shop.id]].name, line: lineText(line), background: shop.background,
      money: this.s.money, permit: this.s.permit, options };
    return this.view && JSON.stringify(this.view) === JSON.stringify(next) ? this.view : next;
  }

  private buildState(): MissionHudState {
    const targets: RouteTarget[] = [PIZZERIA, DEALER, MARKET];
    if (this.s.phase === 'carrying') targets.push(this.dropoffTarget());
    const active = this.s.phase === 'idle' ? null : this.s.phase === 'permit_needed' ? MARKET.id
      : this.s.phase === 'carrying' ? this.currentDelivery.id : PIZZERIA.id;
    const id = this.messageId();
    return {
      targets: this.hud && sameTargets(this.hud.targets, targets) ? this.hud.targets : targets,
      activeTargetId: active,
      money: this.s.money,
      timerSeconds: this.s.phase === 'carrying' ? Math.max(0, Math.ceil(deliveryDeadline(this.s.par) - this.s.elapsed)) : null,
      message: id ? lineText(id) : null,
    };
  }

  private emit() {
    const next = this.buildState();
    const prev = this.hud;
    if (next.targets === prev.targets && next.activeTargetId === prev.activeTargetId && next.money === prev.money
      && next.timerSeconds === prev.timerSeconds && next.message === prev.message) return;
    this.hud = next;
    for (const listener of this.listeners) listener(next);
  }

  private persist() {
    this.savedSecond = Math.floor(this.s.elapsed);
    try {
      this.options.storage?.setItem(SAVE_KEY, JSON.stringify(this.s));
    } catch {
      // Storage full or blocked (private mode): the game continues without persistence.
    }
  }
}

function sameTargets(a: RouteTarget[], b: RouteTarget[]) {
  return a.length === b.length && a.every((t, i) => t.id === b[i].id);
}
