// Deterministic mission state machine: courier permit, shops, pizza pickup, timed delivery, pay and tips
// (docs/design/story.md, "Mission state machine" and "Shops"). It is driven only by update(dt, frame), so tests
// replay it exactly. Static messages use dialogue ids; rotating announcements carry their selected text.
import type { HudStateListener, MissionHudSource, MissionHudState, RouteTarget, Router, WorldPoint } from '../contract';
import {
  basePay, LIMITED_COOLDOWN_SECONDS, LIMITED_OFFER_SECONDS, LONG_UNLOCK_DELIVERIES, DOUBLE_BONUS, KIDNEY_PAYOUT, KIDNEY_RUN_FACTOR, PERMIT_DELIVERIES, PERMIT_MAX, PERMIT_PRICE, PERMIT_WARN_AT,
  CREDIT_PERMIT_DEBT, debtRepayment, HEIST_CASH, PLATES_PRICE, SLICE_PRICE, SLICE_SECONDS, VEHICLE_PRICE, WARNING_PRICE, deliveryDeadline,
  sellBackPrice, tip, type VehicleOwnership, type DeliveryJob,
} from '../economy';
import { DIALOGUE, lineText } from '../content/dialogue';
import { addressSentence, nextPhrase, PHRASES, type PhraseKind } from '../content/announcements';
import type { PoolAddress } from '../content/addresses';
import { DELIVERIES } from '../content/deliveries';
import { DEALER, HEIST_DROP, HEIST_YARD, MARKET, PIZZERIA } from '../content/locations';
import { SHOPS, optionDetail, optionLabel, type OptionId, type Shop, type ShopId } from '../content/shops';
import { drawLimitedOrder, drawOrder, openingOrder } from './orders';
import type { ShopView } from './shopView';
export type { ShopView, ShopOptionView } from './shopView';
import { legMetres, longOrder, orderQuote, type OrderQuote, type StopLeg } from './quotes';
export type { OrderQuote } from './quotes';
import { freshSave, newSeed, parseSave, type HeistStage, type MissionSave, type Phase } from './save';
import { SCRIPTED_IDS, Stops, type OrderStop } from './stops';
import { CARRY_HINT_DELAY, lineSeconds, saidText, type Said } from './speech';
import { applyPoleFine } from '../poles/fine';
export { missionSummary } from './save';
export { lineSeconds } from './speech';

type BoughtVehicle = Exclude<VehicleOwnership, 'none'>;

/** Horizontal distance (m) within which the interaction button acts on a target. */
export const INTERACT_RADIUS = 3;
/** An open shop closes when the player is moved further than this from its zone centre. */
const SHOP_LEAVE_RADIUS = 6;
/** Physics vehicle ids: the player's own car (also the start car); the scooter and truck exist once bought. */
export const OWN_VEHICLE_ID = 'player';
export const SCOOTER_ID = 'scooter';
export const TRUCK_ID = 'truck';
const OWNED_IDS: Record<VehicleOwnership, string | null> = { none: null, scooter: SCOOTER_ID, car: OWN_VEHICLE_ID, truck: TRUCK_ID };
/** The physics vehicle that a vehicle ownership class drives. */
export const ownedVehicleId = (vehicle: VehicleOwnership) => OWNED_IDS[vehicle];
/** "Bring my vehicle" at the pizzeria is greyed out while the vehicle already stands this close (m) to the pizzeria. */
export const BRING_NEAR_M = 30;
/** Seconds of play between two uses of the pause menu's "Bring my vehicle here". */
export const BRING_COOLDOWN_SECONDS = 60;
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
  /** Where the player's vehicle (`MissionSystem.rideId`) stands; null or missing when it is not in the world. */
  rideAt?: WorldPoint | null;
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
  orderIndex: number; stopIndex: number; addressId: string; payout: number; tip: number; routeMeters: number; job: DeliveryJob;
}

/** A moment worth an autosave (core/save.ts): a delivery ended, or something was bought or sold in a shop. */
type Checkpoint = 'delivery' | 'purchase';

interface Action {
  target: RouteTarget;
  /** Prompt base id; the input device's button glyph is appended (`_pad` / `_key`). */
  prompt: string;
  run: () => void;
}

/** `detail` replaces the option's static detail line (optionDetail) when its offer depends on the state. */
interface OptionState { price: number | null; reason: string | null; quote?: OrderQuote; detail?: string }

const SHOP_SPEAKER: Record<ShopId, string> = { market: 'market', pizzeria: 'pizzeria', dealer: 'dealer' };
const SHOP_PROMPT: Record<ShopId, string> = {
  market: 'prompt_enter_market', pizzeria: 'prompt_enter_pizzeria', dealer: 'prompt_enter_dealer',
};
const SHOP_TITLE: Record<ShopId, string> = { market: 'zone_market', pizzeria: 'zone_pizzeria', dealer: 'zone_dealer' };
const NO_MONEY_LINE: Record<ShopId, string> = {
  market: 'market_no_money', pizzeria: 'pizzeria_no_money', dealer: 'dealer_no_money',
};

const distance = (a: WorldPoint, b: WorldPoint) => Math.hypot(a.x - b.x, a.z - b.z);

/** The truck heist finishes when the player gets out within this many metres of the drop-off ... */
export const HEIST_DROP_RADIUS = 8;
/** ... at the road's level, not on the bridge deck above it. */
const HEIST_DROP_LEVEL_M = 2.5;
const atHeistDrop = (p: WorldPoint) => distance(p, HEIST_DROP.point) <= HEIST_DROP_RADIUS
  && (p.y === undefined || Math.abs(p.y - HEIST_DROP.point.y!) <= HEIST_DROP_LEVEL_M);
export type HeistEvent = 'started' | 'delivered' | 'abandoned';

type TipBand = 'fast' | 'ok' | 'slow';

export class MissionSystem implements MissionHudSource {
  private s: MissionSave;
  private queue: (Said & { left: number })[] = [];
  /** Seconds since the last queued line finished; the carry hint repeats the address only after CARRY_HINT_DELAY. */
  private quiet = 0;
  private lastResult: 'delivered' | 'failed' | null = null;
  private frame: MissionFrame | null = null;
  private listeners = new Set<HudStateListener>();
  private hud: MissionHudState;
  private checkpoint: Checkpoint | null = null;
  /** Seconds of slice energy left (not saved: a reload starts without it). */
  private energy = 0;
  private open: { shop: Shop; line: string; text?: string } | null = null;
  private view: ShopView | null = null;
  private quoteCache: { key: string; quote: OrderQuote; firstPar: number } | null = null;
  private longQuoteCache: { key: string; quote: OrderQuote } | null = null;
  private limitedQuoteCache: { key: string; quote: OrderQuote } | null = null;
  private readonly stops: Stops;
  private hintCache: { key: string; said: Said } | null = null;
  /** Seconds until "Bring my vehicle here" works again (not saved). */
  private bringCooldown = 0;
  /** This frame asked for the player's vehicle at the pizzeria kerb (returned by update). */
  private parkAtPizzeria = false;
  /** The vehicle driven in the previous frame: leaving the truck at the heist drop-off finishes the job. */
  private lastVehicleId: string | null = null;
  private heistEvent: HeistEvent | null = null;
  private phrase(kind: PhraseKind): string { return nextPhrase(kind, this.s.phrases ??= {}, this.s.seed); }

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
      this.s.job = 'short';
      this.s.stop = 0;
    }
    this.queue = [];
    this.hintCache = null;
    this.lastResult = null;
    this.energy = 0;
    this.open = null;
    this.view = null;
    this.quoteCache = null;
    this.longQuoteCache = this.limitedQuoteCache = null;
    if (this.s.limited.stops.some(id => !this.stops.resolve(id))) this.s.limited = { ...this.s.limited, stops: [], left: 0 };
    this.checkpoint = null;
    this.bringCooldown = 0;
    this.emit();
  }

  get phase(): Phase { return this.s.phase; }
  get money(): number { return this.s.money; }
  /** Euros of `money` the developer tools added (never reported to the multiplayer server). */
  get devMoney(): number { return this.s.devMoney; }
  get longUnlocked(): boolean { return this.s.shortDeliveries >= LONG_UNLOCK_DELIVERIES; }
  get permit(): number { return this.s.permit; }
  /** Euros still owed for permits rented on credit. */
  get debt(): number { return this.s.debt ?? 0; }
  get vehicle(): VehicleOwnership { return this.s.vehicle; }
  /** The physics vehicle the player delivers with: the owned one (the start car while owning none). */
  get rideId(): string { return ownedVehicleId(this.s.vehicle) ?? OWN_VEHICLE_ID; }
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
  /** The interaction button acts on a shop or the drop-off at the last frame's position (its prompt shows). */
  get interactable(): boolean { return !this.open && this.s.phase !== 'idle' && !!this.frame && this.action(this.frame) !== null; }
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

  /** A pole fine changes the local wallet even if it has less than fifty euros. */
  finePole() {
    this.s.money = applyPoleFine(this.s.money);
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
  update(dt: number, frame: MissionFrame): { bought: BoughtVehicle | null; opened: boolean; checkpoint: Checkpoint | null;
    /** Park the player's vehicle (`rideId`) at the pizzeria kerb (content/locations.ts PIZZERIA_VEHICLE_SPOT). */
    park: boolean;
    /** The truck heist started, was finished (plates earned: the truck leaves, a car waits nearby) or abandoned. */
    heist: HeistEvent | null; } {
    this.frame = frame;
    this.parkAtPizzeria = false;
    this.bringCooldown = Math.max(0, this.bringCooldown - dt);
    let bought: BoughtVehicle | null = null;
    const wasOpen = this.open !== null;
    this.advanceDialogue(dt);
    // Announced once, and only when a long job can actually be offered (missions/orders.ts drawLongOrder).
    if (this.longUnlocked && !this.s.longAnnounced && !this.queue.length && !this.open && !frame.interact && !frame.choose
      && this.longOrder()) {
      this.s.longAnnounced = true;
      this.say('long_unlocked');
      this.checkpoint = 'delivery';
    }
    this.limitedClock(dt, frame);
    this.heistStep(frame);
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
    const heist = this.heistEvent;
    this.heistEvent = null;
    return { bought, opened: !wasOpen && this.open !== null, checkpoint, park: this.parkAtPizzeria, heist };
  }

  /** The truck heist's stage, or null when no job runs (docs/design/story.md, "Truck heist"). */
  get heist(): HeistStage | null { return this.s.heist ?? null; }

  /** Getting into the trophy truck sends it to the bridge; stopping it under the bridge and getting out finishes the
   *  job: the forged plates are the first reward (HEIST_CASH on later runs), and play.ts takes the truck away and parks a car nearby. */
  private heistStep(frame: MissionFrame) {
    const was = this.lastVehicleId;
    this.lastVehicleId = frame.vehicleId;
    if (this.s.heist === 'pickup' && frame.vehicleId === TRUCK_ID) {
      this.s.heist = 'deliver';
      this.say('heist_in_truck');
    } else if (this.s.heist === 'deliver' && was === TRUCK_ID && frame.vehicleId === null && atHeistDrop(frame.position)) {
      this.s.heist = null;
      // The first run earns the plates; every later run (the job is repeatable) pays HEIST_CASH instead.
      if (this.s.plates) this.s.money += HEIST_CASH;
      this.say(this.s.plates ? 'heist_done_cash' : 'heist_done');
      this.s.plates = true;
      this.heistEvent = 'delivered';
      this.checkpoint = 'purchase';
    }
  }

  private heistTarget(): RouteTarget | null {
    return this.s.heist === 'pickup' ? HEIST_YARD : this.s.heist === 'deliver' ? HEIST_DROP : null;
  }

  /** Why the pause menu's "Bring my vehicle here" is unavailable now (a dialogue line id), or null. */
  bringHereReason(): string | null { return this.bringReason(false); }
  /** Seconds until "Bring my vehicle here" is available again. */
  get bringCooldownLeft(): number { return this.bringCooldown; }
  /** The pause menu parked the vehicle beside the player: the notice, and the cooldown starts. */
  broughtHere() {
    this.bringCooldown = BRING_COOLDOWN_SECONDS;
    this.say('notice_vehicle_brought');
    this.emit();
  }
  /** No drivable street with room for the vehicle was found near the player; the cooldown does not start. */
  bringFailed() {
    this.say('notice_no_street_nearby');
    this.emit();
  }

  /** Why the player's vehicle cannot be brought to the pizzeria kerb (`pizzeria`) or beside the player now. */
  private bringReason(pizzeria: boolean): string | null {
    if (this.s.vehicle === 'none') return 'reason_no_own_vehicle';
    if (this.frame?.vehicleId === this.rideId) return 'reason_riding_it';
    if (!pizzeria) return this.bringCooldown > 0 ? 'reason_bring_cooldown' : null;
    const at = this.frame?.rideAt;
    return at && distance(at, PIZZERIA.point) <= BRING_NEAR_M ? 'reason_vehicle_outside' : null;
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
    this.quoteCache = this.longQuoteCache = this.limitedQuoteCache = null;
    this.emit();
    return next;
  }

  /** Runs the LIMITED TIME offer clock in game seconds once long jobs are unlocked: an offer stays on the menu for
   *  LIMITED_OFFER_SECONDS (declining is letting it run out); LIMITED_COOLDOWN_SECONDS after it is taken or runs out
   *  the next one is drawn, when nothing is being said and no menu is open, and announced. */
  private limitedClock(dt: number, frame: MissionFrame) {
    const offer = this.s.limited;
    if (!this.longUnlocked || this.s.phase === 'idle') return;
    offer.left = Math.max(0, offer.left - dt);
    if (offer.left > 0) return;
    if (offer.stops.length) {
      offer.stops = [];
      offer.left = LIMITED_COOLDOWN_SECONDS;
      this.limitedQuoteCache = null;
      return;
    }
    if (this.queue.length || this.open || frame.interact || frame.choose) return;
    const used = new Set([...this.s.delivered, ...this.s.order]);
    const stops = drawLimitedOrder(this.stops.pool, offer.n, this.s.seed, used, (a, b) => this.landmarkLeg(a, b));
    offer.n += 1;
    if (!stops) { offer.left = LIMITED_COOLDOWN_SECONDS; return; }
    offer.stops = stops.map(a => a.id);
    offer.left = LIMITED_OFFER_SECONDS;
    this.say({ id: 'limited_offer', text: addressSentence(this.phrase('limited'), stops[0].address, stops[1]?.address) });
    this.checkpoint = 'delivery';
  }

  /** The current order's stops; draws the next order when none is quoted yet. */
  private ensureOrder(): OrderStop[] {
    if (this.s.order.length === 0) {
      if (this.s.job === 'limited') this.s.job = 'short'; // only ever taken with its stops
      if (this.s.job === 'long') {
        const next = this.longOrder();
        if (next) { this.s.order = next; this.s.stop = 0; return next.map(id => this.stops.resolve(id)!); }
      }
      const n = this.s.shortDeliveries - DELIVERIES.length;
      const draw = n >= 0 ? drawOrder(this.stops.pool.filter(a => !a.landmark && a.foot <= 1200), n, this.s.seed, new Set(this.s.delivered), SCRIPTED_IDS,
        this.s.delivered.slice(-2)) : null;
      if (draw?.exhausted) this.s.delivered = [];
      if (!this.s.order.length) this.s.order = draw ? draw.stops : [this.nextOpening().id];
      this.s.stop = 0;
    }
    return this.s.order.map(id => this.stops.resolve(id)!);
  }

  /** The next scripted delivery: the first of this game's opening order not served yet; without a pool the opening
   *  repeats in that order. */
  private nextOpening() {
    const order = openingOrder(this.s.seed);
    return order.find(d => !this.s.opening.includes(d.id)) ?? order[this.s.shortDeliveries % order.length];
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
      // A quay stall cannot be entered from the bridge deck above it.
      if (c.target.point.y !== undefined && frame.position.y !== undefined
        && Math.abs(c.target.point.y - frame.position.y) > 1.5) continue;
      const d = distance(frame.position, c.target.point);
      if (d <= bestDistance) { best = c; bestDistance = d; }
    }
    return best;
  }

  private enter(shop: Shop) {
    const line = this.greeting(shop.id);
    const text = line in PHRASES ? this.phrase(line as PhraseKind) : undefined;
    this.open = { shop, line, text };
    this.say({ id: line, text });
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
      case 'permit_on_credit':
        return { price: null, reason: s.permit > 0 ? 'reason_credit_permit_left' : s.money >= PERMIT_PRICE ? 'reason_credit_cash' : null };
      case 'forged_plates': return s.plates ? { price: PLATES_PRICE, reason: 'reason_plates_owned' } : money(PLATES_PRICE);
      case 'sell_kidney': return { price: -KIDNEY_PAYOUT, reason: s.kidney ? 'reason_kidney_sold' : null };
      case 'expiry_warning': return s.warning ? { price: WARNING_PRICE, reason: 'reason_warning_owned' } : money(WARNING_PRICE);
      case 'job_truck_heist':
        return { price: null, reason: s.heist ? 'reason_heist_running'
          : s.vehicle === 'truck' ? 'reason_heist_truck_owned' : s.phase === 'carrying' ? 'reason_carrying' : null,
          detail: s.plates ? 'detail_job_truck_heist_cash' : undefined };
      case 'abandon_truck_heist':
        return { price: null, reason: !s.heist ? 'reason_no_heist' : this.frame?.vehicleId === TRUCK_ID ? 'reason_heist_in_truck' : null };
      case 'take_long_order':
      case 'take_limited_order':
      case 'take_order': {
        const long = id !== 'take_order';
        const quote = id === 'take_long_order' ? this.longQuote() : id === 'take_limited_order' ? this.limitedQuote() : this.quote();
        const reason = long && !this.longUnlocked ? 'reason_long_locked'
          : long && !quote ? (id === 'take_long_order' ? 'reason_no_long_route' : 'reason_no_limited')
          : long && this.parVehicle() === 'none' ? 'reason_long_vehicle'
          : s.heist ? 'reason_heist_active'
          : s.phase === 'carrying' ? 'reason_carrying' : s.permit === 0 ? 'reason_no_permit'
          : !this.mayDeliverWith(this.frame?.vehicleId ?? null) ? 'reason_own_vehicle_only' : null;
        return { price: null, reason, quote: quote ?? undefined };
      }
      case 'slice': return money(SLICE_PRICE);
      case 'tip_rules': return { price: null, reason: null };
      case 'bring_vehicle': return { price: null, reason: this.bringReason(true) };
      case 'buy_scooter':
      case 'buy_car':
      case 'buy_truck': {
        const price = VEHICLE_PRICE[SOLD[id]!];
        if (!dealerOpen) return { price, reason: 'reason_no_permit' };
        if (id === 'buy_truck' && s.heist) return { price, reason: 'reason_heist_active' };
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
      case 'permit_on_credit':
        this.s.permit += PERMIT_DELIVERIES;
        this.s.debt = this.debt + CREDIT_PERMIT_DEBT;
        if (this.s.phase === 'permit_needed') this.s.phase = 'at_pizzeria';
        this.reply('market_sold_credit');
        this.checkpoint = 'purchase';
        break;
      case 'forged_plates': this.s.plates = true; this.reply('market_sold_plates'); break;
      case 'sell_kidney': this.s.kidney = true; this.reply('market_sold_kidney'); break;
      case 'expiry_warning': this.s.warning = true; this.reply('market_sold_warning'); break;
      case 'job_truck_heist': this.s.heist = 'pickup'; this.heistEvent = 'started'; this.reply(this.s.plates ? 'heist_briefing_cash' : 'heist_briefing'); break;
      case 'abandon_truck_heist': this.s.heist = null; this.heistEvent = 'abandoned'; this.reply('heist_abandoned'); break;
      case 'take_long_order':
        this.s.job = 'long'; this.s.order = this.longOrder()!; this.quoteCache = null;
        this.pickUp(); this.open = null; break;
      case 'take_limited_order':
        this.s.job = 'limited'; this.s.order = [...this.s.limited.stops]; this.quoteCache = this.limitedQuoteCache = null;
        this.s.limited = { ...this.s.limited, stops: [], left: LIMITED_COOLDOWN_SECONDS };
        this.pickUp(); this.open = null; break;
      case 'take_order': this.s.job = 'short'; this.pickUp(); this.open = null; break;
      case 'slice': this.energy = SLICE_SECONDS; this.reply('pizzeria_slice'); break;
      case 'tip_rules': this.reply('pizzeria_tip_rules'); break;
      case 'bring_vehicle': this.parkAtPizzeria = true; this.reply('pizzeria_bring_vehicle'); break;
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
    const text = id in PHRASES ? this.phrase(id as PhraseKind) : undefined;
    if (this.open) { this.open.line = id; this.open.text = text; }
    this.say({ id, text });
  }

  /** Car-route metres between two pool drop-offs, kept for the session so a quoted double is priced and paid alike. */
  private readonly landmarkLegs = new Map<string, number>();
  private landmarkLeg(a: PoolAddress, b: PoolAddress): number {
    const key = `${a.id}>${b.id}`;
    let metres = this.landmarkLegs.get(key);
    if (metres === undefined) this.landmarkLegs.set(key, metres = this.streetMetres(a.point, b.point, 'car'));
    return metres;
  }

  /** The leg between two drop-offs of the current job: a long-range leg is always driven. */
  private stopLeg(job: DeliveryJob): StopLeg | undefined {
    return job === 'short' ? undefined
      : (a, b) => a.pool && b.pool ? this.landmarkLeg(a.pool, b.pool) : this.streetMetres(a.point, b.point, 'car');
  }

  private longOrder() { return longOrder(this.stops.pool, this.s, (a, b) => this.landmarkLeg(a, b)); }

  /** The next long job's quote, single or double, priced like any order (missions/quotes.ts orderQuote). */
  private longQuote(): OrderQuote | null {
    const ids = this.longOrder();
    if (!ids) return null;
    const vehicle = this.parVehicle();
    const key = `${ids.join(',')}|${vehicle}`;
    if (this.longQuoteCache?.key !== key) {
      const stops = ids.map(id => this.stops.resolve(id)!);
      this.longQuoteCache = { key, quote: orderQuote(stops, vehicle, 'long', (a, b) => this.streetMetres(a, b),
        this.stopLeg('long')).quote };
    }
    return this.longQuoteCache.quote;
  }

  /** The LIMITED TIME offer on the menu with its seconds left, or null between offers. */
  private limitedQuote(): OrderQuote | null {
    const { stops: ids, left } = this.s.limited;
    if (!ids.length) return null;
    const vehicle = this.parVehicle();
    const key = `${ids.join(',')}|${vehicle}`;
    if (this.limitedQuoteCache?.key !== key) {
      const stops = ids.map(id => this.stops.resolve(id)!);
      this.limitedQuoteCache = { key, quote: orderQuote(stops, vehicle, 'limited', (a, b) => this.streetMetres(a, b),
        this.stopLeg('limited')).quote };
    }
    return { ...this.limitedQuoteCache.quote, expires: Math.ceil(left) };
  }

  private quote(): OrderQuote {
    const stops = this.ensureOrder();
    const vehicle = this.parVehicle();
    const key = `${this.s.job}|${this.s.order.join(',')}|${vehicle}`;
    if (this.quoteCache?.key !== key) {
      this.quoteCache = { key, ...orderQuote(stops, vehicle, this.s.job, (a, b) => this.streetMetres(a, b), this.stopLeg(this.s.job)) };
    }
    return this.quoteCache.quote;
  }

  /** Route metres of the current order's legs (pizzeria to the first drop-off, then drop-off to drop-off). */
  private legMetres(stops: OrderStop[]): number[] {
    return legMetres(stops, this.parVehicle(), this.s.job, (a, b) => this.streetMetres(a, b), this.stopLeg(this.s.job));
  }

  /** Street-route metres between two points (in the player's travel mode unless `mode` is given), or the straight
   *  line when either end is off the graph. */
  private streetMetres(from: WorldPoint, to: WorldPoint, mode?: 'foot' | 'car'): number {
    const path = this.options.router.route(from, to, mode);
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
    this.say({ id: second ? 'order_double' : 'order_single',
      text: addressSentence(this.phrase(second ? 'double' : 'order'), first.address, second?.address) });
  }

  private handOver() {
    const stops = this.ensureOrder();
    const stop = stops[this.s.stop];
    const last = this.s.stop === stops.length - 1;
    const t = tip(this.s.elapsed, last ? this.s.par : this.s.firstPar);
    const bonus = stops.length > 1 && last && t > 0 ? DOUBLE_BONUS : 0;
    // Each pizza pays its own leg: the second of a long double pays for the drive between the two landmarks.
    const metres = this.legMetres(stops)[this.s.stop];
    const pay = basePay(metres, this.s.job);
    // Spending, like the permit: the multiplayer payout below stays the earned pay.
    const repaid = debtRepayment(this.debt, pay);
    this.s.debt = this.debt - repaid;
    this.s.money += pay + t + bonus - repaid;
    this.options.onPaid?.({ orderIndex: this.s.delivery, stopIndex: this.s.stop,
      addressId: stop.pool?.id ?? stop.id, job: this.s.job, payout: pay + bonus, tip: t, routeMeters: metres });
    this.s.pizzas += 1;
    if (!stop.scripted) this.s.delivered.push(stop.id);
    const band: TipBand = t >= 5 ? 'fast' : t >= 1 ? 'ok' : 'slow';
    const customer: Said = { id: `customer_${band}`, text: this.phrase(`customer_${band}`) };
    const lines: (string | Said)[] = [customer, ...(this.s.job === 'long' ? [{ id: 'paid_long', text: `Long delivery: ${pay} € + tip ${t} €` }]
      : this.s.job === 'limited' ? [{ id: 'paid_limited', text: `Limited-time delivery: ${pay} € + tip ${t} €` }] : [`paid_tip_${t}`]), ...(bonus ? ['paid_double_bonus'] : []),
      ...(repaid ? [{ id: 'paid_debt', text: this.s.debt > 0 ? `Debt: ${repaid} € of the base pay repaid, ${this.s.debt} € left.`
        : `Debt: ${repaid} € repaid. The market is paid off.` }] : [])];
    if (!last) {
      this.s.stop += 1; // the second pizza of a double: the timer keeps running
      this.say(...lines, { id: 'second_address',
        text: addressSentence(this.phrase('second'), stops[1].address) });
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
    for (const stop of stops) if (stop.scripted && !this.s.opening.includes(stop.id)) this.s.opening.push(stop.id);
    this.s.lastAddress = stops[stops.length - 1].address;
    this.s.phase = result;
    this.s.delivery += 1;
    if (this.s.job === 'short') this.s.shortDeliveries += 1;
    else if (this.s.job === 'long') this.s.longDeliveries += 1;
    this.s.job = 'short';
    this.s.order = [];
    this.s.stop = 0;
    this.s.elapsed = this.s.par = this.s.firstPar = 0;
    this.lastResult = result;
    this.checkpoint = 'delivery';
  }

  private permitWarning(): Said[] {
    return this.s.warning && this.s.permit <= PERMIT_WARN_AT
      ? [{ id: `permit_left_${this.s.permit}`, text: this.phrase(`permit_${this.s.permit}` as PhraseKind) }] : [];
  }

  private hint(key: string, kind: PhraseKind, address?: string): Said {
    if (this.hintCache?.key === key) return this.hintCache.said;
    const said = { id: kind, text: address ? addressSentence(this.phrase(kind), address) : this.phrase(kind) };
    this.hintCache = { key, said };
    return said;
  }

  // ---------------------------------------------------------------------------------------- dialogue, state
  /** Replaces whatever is being said with these lines, shown one after another. */
  private say(...lines: (string | Said)[]) {
    this.hintCache = null;
    this.quiet = 0;
    this.queue = lines.map(line => {
      const said: Said = typeof line === 'string' ? { id: line } : line;
      if (!said.text && said.id in PHRASES) said.text = this.phrase(said.id as PhraseKind);
      return { ...said, left: lineSeconds(saidText(said)) };
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
    if (!this.queue.length) this.quiet += left;
  }

  private message(): Said | null {
    if (this.queue.length) return this.queue[0];
    if (this.open) return null; // the menu shows the shopkeeper; no prompt behind it
    const action = this.frame ? this.action(this.frame) : null;
    if (action && this.s.phase !== 'idle') return { id: `${action.prompt}_${this.frame!.input === 'keyboard' ? 'key' : 'pad'}` };
    if (this.s.heist && this.s.phase !== 'idle') return { id: this.s.heist === 'pickup' ? 'hint_heist_pickup' : 'hint_heist_deliver' };
    switch (this.s.phase) {
      case 'idle': return null;
      case 'permit_needed': return this.hint(`permit:${this.s.delivery}:${this.s.permit}`, 'hint_permit');
      case 'carrying': return this.quiet < CARRY_HINT_DELAY ? null : this.hint(`carry:${this.s.delivery}:${this.s.stop}`, 'hint_carry', this.currentStop.address);
      default: return this.hint(`pizzeria:${this.s.delivery}`, 'hint_pizzeria');
    }
  }

  private buildView(): ShopView | null {
    if (!this.open) return null;
    const { shop, line } = this.open;
    const options = shop.options.map(o => {
      const state = this.optionState(o.id);
      const detail = state.detail ?? optionDetail(o.id);
      return { id: o.id, label: lineText(optionLabel(o.id)), detail: detail ? lineText(detail) : null, price: state.price,
        icon: o.icon, enabled: state.reason === null, reason: state.reason ? lineText(state.reason) : null,
        quote: state.quote ?? null };
    });
    const next: ShopView = { id: shop.id, title: lineText(SHOP_TITLE[shop.id]),
      speaker: DIALOGUE.speakers[SHOP_SPEAKER[shop.id]].name, line: this.open.text ?? lineText(line), background: shop.background,
      money: this.s.money, permit: this.s.permit, debt: this.debt, delivered: this.s.pizzas, options };
    return this.view && JSON.stringify(this.view) === JSON.stringify(next) ? this.view : next;
  }

  private buildState(): MissionHudState {
    const targets: RouteTarget[] = [PIZZERIA, DEALER, MARKET];
    if (this.s.phase === 'carrying') targets.push(this.dropoffTarget());
    const heist = this.heistTarget();
    if (heist) targets.push(heist);
    const active = this.s.phase === 'idle' ? null : this.s.phase === 'carrying' ? this.currentStop.id
      : heist ? heist.id : this.s.phase === 'permit_needed' ? MARKET.id : PIZZERIA.id;
    const said = this.message();
    return {
      targets: this.hud && sameTargets(this.hud.targets, targets) ? this.hud.targets : targets,
      activeTargetId: active,
      money: this.s.money,
      timerSeconds: this.s.phase === 'carrying' ? Math.max(0, Math.ceil(deliveryDeadline(this.s.par) - this.s.elapsed)) : null,
      message: said ? saidText(said) : null,
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
