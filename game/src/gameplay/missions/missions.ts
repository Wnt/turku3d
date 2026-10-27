// Deterministic mission state machine: courier permit, shops, pizza pickup, timed delivery, pay and tips
// (docs/design/story.md, "Mission state machine" and "Shops"). It is driven only by update(dt, frame), so tests
// replay it exactly. Messages are dialogue line ids resolved to their exact text (content/dialogue.json).
import type { HudStateListener, MissionHudSource, MissionHudState, RouteTarget, Router, WorldPoint } from '../contract';
import {
  BASE_PAY, DOUBLE_BONUS, KIDNEY_PAYOUT, KIDNEY_RUN_FACTOR, PERMIT_DELIVERIES, PERMIT_MAX, PERMIT_PRICE, PERMIT_WARN_AT,
  PLATES_PRICE, SLICE_PRICE, SLICE_SECONDS, VEHICLE_PRICE, WARNING_PRICE, deliveryDeadline, parSeconds,
  sellBackPrice, tip, type VehicleOwnership,
} from '../economy';
import { DIALOGUE, lineText, type LineVars } from '../content/dialogue';
import type { PoolAddress } from '../content/addresses';
import { DELIVERIES } from '../content/deliveries';
import { DEALER, MARKET, PIZZERIA } from '../content/locations';
import { SHOPS, optionDetail, optionLabel, type OptionId, type Shop, type ShopId } from '../content/shops';
import { drawOrder, pickLine } from './orders';
import { freshSave, newSeed, parseSave, type MissionSave, type Phase } from './save';
import { SCRIPTED_IDS, Stops, type OrderStop } from './stops';

type BoughtVehicle = Exclude<VehicleOwnership, 'none'>;

/** Horizontal distance (m) within which the interaction button acts on a target. */
export const INTERACT_RADIUS = 3;
/** An open shop closes when the player is moved further than this from its zone centre. */
const SHOP_LEAVE_RADIUS = 6;
/** Physics vehicle ids: the player's own car (also the start car), and the scooter and the trophy truck, which exist
 *  once bought. */
export const OWN_VEHICLE_ID = 'player';
export const SCOOTER_ID = 'scooter';
export const TRUCK_ID = 'truck';
const OWNED_IDS: Record<VehicleOwnership, string | null> = { none: null, scooter: SCOOTER_ID, car: OWN_VEHICLE_ID, truck: TRUCK_ID };
/** The physics vehicle that a vehicle ownership class drives. */
export const ownedVehicleId = (vehicle: VehicleOwnership) => OWNED_IDS[vehicle];
/** The dealer's purchase options and the vehicle each one sells. */
const SOLD: Partial<Record<OptionId, BoughtVehicle>> = { buy_scooter: 'scooter', buy_car: 'car', buy_truck: 'truck' };

export interface MissionFrame {
  /** Player feet (on foot) or vehicle position (driving). */
  position: WorldPoint;
  /** The interaction button was pressed this frame (opens a shop or hands over a pizza). */
  interact: boolean;
  /** Selects the button glyph in prompts. */
  input: 'keyboard' | 'gamepad' | 'touch';
  /** Physics id of the vehicle the player drives, or null on foot. */
  vehicleId: string | null;
  /** The menu option the player selected this frame in the open shop. */
  choose?: OptionId | null;
  /** The player closed the open shop this frame. */
  leave?: boolean;
}

interface MissionOptions {
  router: Router;
  /** Published delivery addresses (content/addresses.ts); empty: the scripted deliveries repeat. */
  addresses?: readonly PoolAddress[];
  /** Seed of a new game (default random); a saved game keeps its own. */
  seed?: number;
  /** One event per paid drop-off; excludes purchases, starting money and other rewards. */
  onPaid?: (drop: PaidDropoff) => void;
}

export interface PaidDropoff {
  orderIndex: number; stopIndex: number; addressId: string; payout: number; tip: number; routeMeters: number;
}

/** A moment worth an autosave (core/save.ts): a delivery ended, or something was bought or sold in a shop. */
type Checkpoint = 'delivery' | 'purchase';

/** What a save slot shows about a mission save without restoring it. */
interface MissionSummary { deliveries: number; money: number; devMoney: number; lastAddress: string | null }

/** Slot details of a `MissionSystem.serialize()` text (an invalid text reads as a new game). */
export function missionSummary(text: string | null): MissionSummary {
  const s = parseSave(text, 0);
  return { deliveries: s.delivery, money: s.money, devMoney: s.devMoney, lastAddress: s.lastAddress };
}

/** What the pizzeria quotes before an order is accepted. */
export interface OrderQuote {
  /** The drop-off address, or both joined with ` + ` for a double order. */
  address: string;
  metres: number;
  par: number;
  stops: number;
}

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
  /** Pizzas handed over so far. */
  delivered: number;
  options: ShopOptionView[];
}

interface Action {
  target: RouteTarget;
  /** Prompt base id; the input device's button glyph is appended (`_pad` / `_key`). */
  prompt: string;
  run: () => void;
}

interface OptionState { price: number | null; reason: string | null; quote?: OrderQuote }

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

const CUSTOMER_LINES = 12;
const ORDER_LINES = 6;
type TipBand = 'fast' | 'ok' | 'slow';
interface Said { id: string; vars?: LineVars }

export class MissionSystem implements MissionHudSource {
  private s: MissionSave;
  private queue: (Said & { left: number })[] = [];
  private lastResult: 'delivered' | 'failed' | null = null;
  private frame: MissionFrame | null = null;
  private listeners = new Set<HudStateListener>();
  private hud: MissionHudState;
  private checkpoint: Checkpoint | null = null;
  /** Seconds of slice energy left (not saved: a reload starts without it). */
  private energy = 0;
  private open: { shop: Shop; line: string } | null = null;
  private view: ShopView | null = null;
  private quoteCache: { key: string; quote: OrderQuote; firstPar: number } | null = null;
  private readonly stops: Stops;

  constructor(private readonly options: MissionOptions) {
    this.stops = new Stops(options.addresses ?? [], options.router);
    this.s = freshSave(options.seed ?? newSeed());
    this.hud = this.buildState();
  }

  /** The mission save as text; its format and migrations stay private to this module (missions/save.ts). */
  serialize(): string { return JSON.stringify(this.s); }

  /** Continues from a `serialize()` text, or starts a new game with null. A running order keeps its timer. */
  restore(text: string | null) {
    this.s = parseSave(text, this.options.seed ?? newSeed());
    if (this.s.order.some(id => !this.stops.resolve(id))) {
      // The saved order's address left the published area: the pizza goes back and its permit delivery returns.
      if (this.s.phase === 'carrying') { this.s.permit += 1; this.s.phase = this.restPhase(); }
      this.s.order = [];
      this.s.stop = 0;
    }
    this.queue = [];
    this.lastResult = null;
    this.energy = 0;
    this.open = null;
    this.view = null;
    this.quoteCache = null;
    this.checkpoint = null;
    this.emit();
  }

  get phase(): Phase { return this.s.phase; }
  get money(): number { return this.s.money; }
  /** Euros of `money` the developer tools added (never reported to the multiplayer server). */
  get devMoney(): number { return this.s.devMoney; }
  get permit(): number { return this.s.permit; }
  get vehicle(): VehicleOwnership { return this.s.vehicle; }
  get plates(): boolean { return this.s.plates; }
  get kidney(): boolean { return this.s.kidney; }
  /** Pizzas handed over. */
  get pizzas(): number { return this.s.pizzas; }
  /** Finished orders (delivered or failed). */
  get deliveries(): number { return this.s.delivery; }
  /** Address of the last finished order's last drop-off, or null before the first one. */
  get lastAddress(): string | null { return this.s.lastAddress; }
  /** The drops of the quoted or carried order (drawn on first use). */
  get order(): OrderStop[] { return this.ensureOrder(); }
  /** The next drop-off of the quoted or carried order. */
  get currentStop(): OrderStop { return this.order[this.s.stop]; }
  /** Multiplier on the player's run and sprint speed. */
  get runFactor(): number { return this.s.kidney && this.energy <= 0 ? KIDNEY_RUN_FACTOR : 1; }
  /** The open shop menu, or null. The same object is returned until something in it changes. */
  get shop(): ShopView | null { return this.view; }
  snapshot() {
    const order = this.s.order.map(id => this.stops.resolve(id)!);
    return { ...this.s, address: order[this.s.stop]?.address ?? null, stops: order.map(o => o.address),
      dialogue: this.queue.map(q => q.id), energy: this.energy, runFactor: this.runFactor, shop: this.view, hud: this.hud };
  }

  subscribe(listener: HudStateListener): () => void {
    this.listeners.add(listener);
    listener(this.hud);
    return () => { this.listeners.delete(listener); };
  }

  /** Developer tools (localhost only): adds `euros` to the local wallet and to `devMoney`, never a paid drop-off. */
  grantDevMoney(euros: number) {
    this.s.money += euros;
    this.s.devMoney += euros;
    this.emit();
  }

  /** Leaves idle once the player starts playing; a continued game keeps its saved phase. */
  begin() {
    if (this.s.phase !== 'idle') return;
    this.s.phase = this.restPhase();
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
  update(dt: number, frame: MissionFrame): { bought: BoughtVehicle | null; opened: boolean; checkpoint: Checkpoint | null } {
    this.frame = frame;
    let bought: BoughtVehicle | null = null;
    const wasOpen = this.open !== null;
    this.advanceDialogue(dt);
    this.energy = Math.max(0, this.energy - dt);
    if ((this.s.phase === 'delivered' || this.s.phase === 'failed') && this.queue.length === 0) {
      this.s.phase = this.restPhase();
    }
    if (this.s.phase === 'carrying' && dt > 0) {
      this.s.elapsed += dt;
      if (this.s.elapsed > deliveryDeadline(this.s.par)) this.fail();
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
    const checkpoint = this.checkpoint;
    this.checkpoint = null;
    return { bought, opened: !wasOpen && this.open !== null, checkpoint };
  }

  // ----------------------------------------------------------------------------------------------- actions
  private owns(vehicleId: string) { return vehicleId === ownedVehicleId(this.s.vehicle); }
  private mayDeliverWith(vehicleId: string | null) { return vehicleId === null || this.owns(vehicleId) || this.s.plates; }
  private restPhase(): Phase { return this.s.permit > 0 ? 'at_pizzeria' : 'permit_needed'; }

  private dropoffTarget(): RouteTarget {
    const d = this.currentStop;
    return { id: d.id, kind: 'dropoff', label: d.address, point: d.point };
  }

  /** Moves a drop-off whose point the ground check rejected (missions/dropoffs.ts) to its next street-level
   *  candidate (`Stops.relocate`); null when every candidate was rejected. */
  relocateDropoff(id: string, point: WorldPoint): WorldPoint | null {
    const next = this.stops.relocate(id, point);
    this.quoteCache = null;
    this.emit();
    return next;
  }

  /** The current order's stops; draws the next order when none is quoted yet. */
  private ensureOrder(): OrderStop[] {
    if (this.s.order.length === 0) {
      const n = this.s.delivery - DELIVERIES.length;
      const draw = n >= 0 ? drawOrder(this.stops.pool, n, this.s.seed, new Set(this.s.delivered), SCRIPTED_IDS,
        this.s.delivered.slice(-2)) : null;
      if (draw?.exhausted) this.s.delivered = [];
      this.s.order = draw ? draw.stops : [DELIVERIES[this.s.delivery % DELIVERIES.length].id];
      this.s.stop = 0;
    }
    return this.s.order.map(id => this.stops.resolve(id)!);
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
      case 'buy_scooter':
      case 'buy_car':
      case 'buy_truck': {
        const price = VEHICLE_PRICE[SOLD[id]!];
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
      case 'buy_scooter':
      case 'buy_car':
      case 'buy_truck':
        this.s.vehicle = SOLD[id]!;
        this.say(`dealer_sold_${this.s.vehicle}`);
        this.open = null; // the vehicle waits at the kerb
        break;
      case 'sell_vehicle': this.s.vehicle = 'none'; this.reply('dealer_bought_back'); break;
    }
    if (price !== null) this.checkpoint = 'purchase';
  }

  /** The shopkeeper answers inside the open menu; the line is also the HUD message, so it is voiced. */
  private reply(id: string) {
    if (this.open) this.open.line = id;
    this.say(id);
  }

  private quote(): OrderQuote {
    const stops = this.ensureOrder();
    const vehicle = this.parVehicle();
    const key = `${this.s.order.join(',')}|${vehicle}`;
    if (this.quoteCache?.key !== key) {
      const first = this.metresFromPizzeria(stops[0], vehicle);
      const firstPar = parSeconds(first, vehicle);
      const leg = stops.length > 1 ? this.streetMetres(stops[0].point, stops[1].point) : 0;
      const par = stops.length > 1 ? firstPar + parSeconds(leg, vehicle) : firstPar;
      this.quoteCache = { key, firstPar: stops.length > 1 ? firstPar : 0,
        quote: { address: stops.map(o => o.address).join(' + '), metres: first + leg, par, stops: stops.length } };
    }
    return this.quoteCache.quote;
  }

  /** Route metres from the pizzeria: the published walking or driving distance of a pool address (every vehicle
   *  rides the streets), the router's path for a scripted one. */
  private metresFromPizzeria(stop: OrderStop, vehicle: VehicleOwnership): number {
    if (stop.pool) return vehicle === 'none' ? stop.pool.foot : stop.pool.car ?? stop.pool.foot;
    return this.streetMetres(PIZZERIA.point, stop.point);
  }

  /** Street-route metres between two points, or the straight line when either end is off the graph. */
  private streetMetres(from: WorldPoint, to: WorldPoint): number {
    const path = this.options.router.route(from, to);
    return path ? this.options.router.distance(path) : distance(from, to);
  }

  /** Par speed class: the owned vehicle, or a car when a borrowed car is used with forged plates. */
  private parVehicle(): VehicleOwnership {
    const id = this.frame?.vehicleId ?? null;
    return id !== null && !this.owns(id) && this.s.plates ? 'car' : this.s.vehicle;
  }

  private pickUp() {
    const stops = this.ensureOrder();
    this.s.par = this.quote().par;
    this.s.firstPar = this.quoteCache!.firstPar;
    this.s.stop = 0;
    this.s.elapsed = 0;
    this.s.permit -= 1;
    this.s.phase = 'carrying';
    const [first, second] = stops;
    if (first.scripted) this.say(first.scripted.handover);
    else {
      this.say(`pizzeria_order_${pickLine(this.s.seed, this.s.delivery, ORDER_LINES) + 1}`,
        second ? { id: 'order_next_double', vars: { address: first.address, next: second.address } }
          : { id: 'order_next', vars: { address: first.address } });
    }
  }

  private handOver() {
    const stops = this.ensureOrder();
    const stop = stops[this.s.stop];
    const last = this.s.stop === stops.length - 1;
    const t = tip(this.s.elapsed, last ? this.s.par : this.s.firstPar);
    const bonus = stops.length > 1 && last && t > 0 ? DOUBLE_BONUS : 0;
    this.s.money += BASE_PAY + t + bonus;
    this.options.onPaid?.({ orderIndex: this.s.delivery, stopIndex: this.s.stop,
      addressId: stop.pool?.id ?? stop.id, payout: BASE_PAY + bonus, tip: t,
      routeMeters: this.s.stop === 0 ? this.metresFromPizzeria(stop, this.parVehicle())
        : this.streetMetres(stops[this.s.stop - 1].point, stop.point) });
    this.s.pizzas += 1;
    if (!stop.scripted) this.s.delivered.push(stop.id);
    const band: TipBand = t >= 5 ? 'fast' : t >= 1 ? 'ok' : 'slow';
    const customer = stop.scripted ? stop.scripted.customer[band]
      : `customer_${band}_${pickLine(this.s.seed, 1000 + this.s.pizzas, CUSTOMER_LINES) + 1}`;
    const lines = [customer, `paid_tip_${t}`, ...(bonus ? ['paid_double_bonus'] : [])];
    if (!last) {
      this.s.stop += 1; // the second pizza of a double: the timer keeps running
      this.say(...lines);
      this.checkpoint = 'delivery';
      return;
    }
    this.finishOrder('delivered');
    this.say(...lines, ...this.permitWarning());
  }

  private fail() {
    // The customers left waiting cancel; their addresses count as served.
    for (const stop of this.ensureOrder().slice(this.s.stop)) if (!stop.scripted) this.s.delivered.push(stop.id);
    this.finishOrder('failed');
    this.say('delivery_failed', ...this.permitWarning());
  }

  private finishOrder(result: 'delivered' | 'failed') {
    const stops = this.ensureOrder();
    this.s.lastAddress = stops[stops.length - 1].address;
    this.s.phase = result;
    this.s.delivery += 1;
    this.s.order = [];
    this.s.stop = 0;
    this.s.elapsed = this.s.par = this.s.firstPar = 0;
    this.lastResult = result;
    this.checkpoint = 'delivery';
  }

  private permitWarning(): string[] {
    return this.s.warning && this.s.permit <= PERMIT_WARN_AT ? [`permit_left_${this.s.permit}`] : [];
  }

  /** The step hint while carrying: the scripted line, or the pool address (and the second one of a double). */
  private carryingHint(): Said {
    const stops = this.ensureOrder();
    const stop = stops[this.s.stop];
    if (stop.scripted) return { id: stop.scripted.hint };
    if (stops.length === 1) return { id: 'hint_deliver_pool', vars: { address: stop.address } };
    return this.s.stop === 0 ? { id: 'hint_deliver_first', vars: { address: stop.address, next: stops[1].address } }
      : { id: 'hint_deliver_second', vars: { address: stop.address } };
  }

  // ---------------------------------------------------------------------------------------- dialogue, state
  /** Replaces whatever is being said with these lines, shown one after another. */
  private say(...lines: (string | Said)[]) {
    this.queue = lines.map(line => {
      const said = typeof line === 'string' ? { id: line } : line;
      return { ...said, left: lineSeconds(lineText(said.id, said.vars)) };
    });
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

  private message(): Said | null {
    if (this.queue.length) return this.queue[0];
    if (this.open) return null; // the menu shows the shopkeeper; no prompt behind it
    const action = this.frame ? this.action(this.frame) : null;
    if (action && this.s.phase !== 'idle') return { id: `${action.prompt}_${this.frame!.input === 'keyboard' ? 'key' : 'pad'}` };
    switch (this.s.phase) {
      case 'idle': return null;
      case 'permit_needed': return { id: 'hint_permit' };
      case 'carrying': return this.carryingHint();
      default: return { id: 'hint_pizzeria' };
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
      money: this.s.money, permit: this.s.permit, delivered: this.s.pizzas, options };
    return this.view && JSON.stringify(this.view) === JSON.stringify(next) ? this.view : next;
  }

  private buildState(): MissionHudState {
    const targets: RouteTarget[] = [PIZZERIA, DEALER, MARKET];
    if (this.s.phase === 'carrying') targets.push(this.dropoffTarget());
    const active = this.s.phase === 'idle' ? null : this.s.phase === 'permit_needed' ? MARKET.id
      : this.s.phase === 'carrying' ? this.currentStop.id : PIZZERIA.id;
    const said = this.message();
    return {
      targets: this.hud && sameTargets(this.hud.targets, targets) ? this.hud.targets : targets,
      activeTargetId: active,
      money: this.s.money,
      timerSeconds: this.s.phase === 'carrying' ? Math.max(0, Math.ceil(deliveryDeadline(this.s.par) - this.s.elapsed)) : null,
      message: said ? lineText(said.id, said.vars) : null,
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
}

function sameTargets(a: RouteTarget[], b: RouteTarget[]) {
  return a.length === b.length
    && a.every((t, i) => t.id === b[i].id && t.point.x === b[i].point.x && t.point.z === b[i].point.z);
}
