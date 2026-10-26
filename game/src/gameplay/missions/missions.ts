// Deterministic mission state machine: licence, pizza pickup, timed delivery, pay and tips, vehicle purchases
// (docs/design/story.md, "Mission state machine"). It is driven only by update(dt, frame), so tests replay it
// exactly. Messages are dialogue line ids resolved to their exact text (content/dialogue.json).
import type { HudStateListener, MissionHudSource, MissionHudState, RouteTarget, Router, WorldPoint } from '../contract';
import {
  BASE_PAY, LICENCE_PRICE, START_MONEY, VEHICLE_PRICE, deliveryDeadline, nextVehicleOffer, parSeconds, tip,
  type VehicleOwnership,
} from '../economy';
import { lineText } from '../content/dialogue';
import { DELIVERIES, type Delivery } from '../content/deliveries';
import { DEALER, MARKET, PIZZERIA } from '../content/locations';

type Phase = 'idle' | 'licence_needed' | 'at_pizzeria' | 'carrying' | 'delivered' | 'failed';
type BoughtVehicle = Exclude<VehicleOwnership, 'none'>;

/** Horizontal distance (m) within which the interaction button acts on a target. */
export const INTERACT_RADIUS = 3;
/** Characters greet once when the player comes this close, and again after leaving GREET_REARM. */
const GREET_RADIUS = 10;
const GREET_REARM = 16;
/** The player's own car (physics vehicle id); it also stands in for the bicycle. */
export const OWN_VEHICLE_ID = 'player';
export const SAVE_KEY = 'turku3d.missions';

interface MissionSave {
  version: 1;
  phase: Phase;
  money: number;
  licence: boolean;
  vehicle: VehicleOwnership;
  /** Index of the current or next delivery; it grows without bound and wraps through DELIVERIES. */
  delivery: number;
  /** Seconds since pickup and the par time of the delivery being carried. */
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
  /** The interaction button was pressed this frame. */
  interact: boolean;
  /** Selects the button glyph in prompts. */
  input: 'keyboard' | 'gamepad';
  /** Physics id of the vehicle the player drives, or null on foot. */
  vehicleId: string | null;
}

interface MissionOptions {
  router: Router;
  storage: KeyValueStore | null;
}

interface Action {
  target: RouteTarget;
  /** Line shown while in range: a prompt base id (button glyph appended) or a full notice id. */
  prompt: { id: string; button: boolean } | null;
  run: () => void;
}

const VALID_PHASES: readonly Phase[] = ['idle', 'licence_needed', 'at_pizzeria', 'carrying', 'delivered', 'failed'];
const VALID_VEHICLES: readonly VehicleOwnership[] = ['none', 'bicycle', 'car'];

/** Seconds a dialogue line stays on the message line: long enough to read it or hear it spoken. */
export function lineSeconds(text: string): number {
  return Math.min(7, Math.max(2.5, 1.2 + text.length * 0.06));
}

const distance = (a: WorldPoint, b: WorldPoint) => Math.hypot(a.x - b.x, a.z - b.z);

function freshSave(): MissionSave {
  return { version: 1, phase: 'idle', money: START_MONEY, licence: false, vehicle: 'none', delivery: 0, elapsed: 0, par: 0 };
}

function parseSave(raw: string | null): MissionSave {
  if (!raw) return freshSave();
  try {
    const s = JSON.parse(raw) as Partial<MissionSave>;
    const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
    if (s.version !== 1 || !VALID_PHASES.includes(s.phase as Phase) || !num(s.money) || typeof s.licence !== 'boolean'
      || !VALID_VEHICLES.includes(s.vehicle as VehicleOwnership) || !num(s.delivery) || !num(s.elapsed) || !num(s.par)) {
      return freshSave();
    }
    const save = s as MissionSave;
    // A finished delivery's result line is not replayed after a reload: the next step is the pizzeria.
    if (save.phase === 'delivered' || save.phase === 'failed') save.phase = 'at_pizzeria';
    if (save.phase === 'carrying' && save.par <= 0) save.phase = 'at_pizzeria';
    return { ...save, money: Math.round(save.money), delivery: Math.floor(save.delivery) };
  } catch {
    return freshSave();
  }
}

export class MissionSystem implements MissionHudSource {
  private s: MissionSave;
  private queue: { id: string; left: number }[] = [];
  private greeted = new Set<string>();
  private lastResult: 'delivered' | 'failed' | null = null;
  private frame: MissionFrame | null = null;
  private listeners = new Set<HudStateListener>();
  private hud: MissionHudState;
  private savedSecond = -1;

  constructor(private readonly options: MissionOptions) {
    this.s = parseSave(options.storage?.getItem(SAVE_KEY) ?? null);
    this.hud = this.buildState();
  }

  get phase(): Phase { return this.s.phase; }
  get money(): number { return this.s.money; }
  get licence(): boolean { return this.s.licence; }
  get vehicle(): VehicleOwnership { return this.s.vehicle; }
  /** True until begin() starts a new game: the start screen shows the intro then. */
  get isNewGame(): boolean { return this.s.phase === 'idle'; }
  get currentDelivery(): Delivery { return DELIVERIES[this.s.delivery % DELIVERIES.length]; }
  snapshot() {
    return { ...this.s, address: this.currentDelivery.address, dialogue: this.queue.map(q => q.id), hud: this.hud };
  }

  subscribe(listener: HudStateListener): () => void {
    this.listeners.add(listener);
    listener(this.hud);
    return () => { this.listeners.delete(listener); };
  }

  /** Leaves idle once the player starts playing; a continued game keeps its saved phase. */
  begin() {
    if (this.s.phase !== 'idle') return;
    this.s.phase = this.s.licence ? 'at_pizzeria' : 'licence_needed';
    this.persist();
    this.emit();
  }

  /** Whether the player may enter this vehicle now. While carrying, only the player's own vehicle counts. */
  mayDrive(vehicleId: string): boolean {
    if (this.s.phase !== 'carrying' || this.owns(vehicleId)) return true;
    this.say('notice_own_vehicle_only');
    this.emit();
    return false;
  }

  /** Advances timers and dialogue by dt seconds (0 while paused) and handles the interaction button. */
  update(dt: number, frame: MissionFrame): { bought: BoughtVehicle | null } {
    this.frame = frame;
    let bought: BoughtVehicle | null = null;
    this.advanceDialogue(dt);
    if ((this.s.phase === 'delivered' || this.s.phase === 'failed') && this.queue.length === 0) {
      this.s.phase = 'at_pizzeria';
      this.persist();
    }
    if (this.s.phase === 'carrying' && dt > 0) {
      this.s.elapsed += dt;
      if (this.s.elapsed > deliveryDeadline(this.s.par)) this.fail();
      else if (Math.floor(this.s.elapsed) !== this.savedSecond) this.persist();
    }
    if (this.s.phase !== 'idle') this.greet(frame.position);
    if (frame.interact && this.s.phase !== 'idle') {
      const action = this.action(frame);
      if (action) {
        const before = this.s.vehicle;
        action.run();
        if (this.s.vehicle !== before && this.s.vehicle !== 'none') bought = this.s.vehicle;
      }
    }
    this.emit();
    return { bought };
  }

  // ----------------------------------------------------------------------------------------------- actions
  private owns(vehicleId: string) { return vehicleId === OWN_VEHICLE_ID && this.s.vehicle !== 'none'; }

  private dropoffTarget(): RouteTarget {
    const d = this.currentDelivery;
    return { id: d.id, kind: 'dropoff', label: d.address, point: d.point };
  }

  /** The action of the nearest target in range, for the current phase. */
  private action(frame: MissionFrame): Action | null {
    const candidates: Action[] = [];
    const phase = this.s.phase;
    if (phase === 'licence_needed') {
      const affordable = this.s.money >= LICENCE_PRICE;
      candidates.push({ target: MARKET, prompt: affordable ? { id: 'prompt_buy_licence', button: true } : { id: 'notice_licence_price', button: false },
        run: () => affordable ? this.buyLicence() : this.say('market_no_money') });
    }
    if (phase === 'at_pizzeria' || phase === 'delivered' || phase === 'failed') {
      const allowed = frame.vehicleId === null || this.owns(frame.vehicleId);
      candidates.push({ target: PIZZERIA, prompt: allowed ? { id: 'prompt_take_pizza', button: true } : { id: 'notice_own_vehicle_only', button: false },
        run: () => { if (allowed) this.pickUp(); } });
    }
    if (phase === 'carrying') {
      candidates.push({ target: this.dropoffTarget(), prompt: { id: 'prompt_hand_over', button: true }, run: () => this.handOver() });
    }
    if (!this.s.licence) {
      // Spending the start money on wheels first would leave too little for the licence.
      candidates.push({ target: DEALER, prompt: { id: 'notice_licence_first', button: false }, run: () => this.say('dealer_no_licence') });
    } else {
      const offer = nextVehicleOffer(this.s.vehicle, this.s.money);
      if (!offer) candidates.push({ target: DEALER, prompt: null, run: () => this.say('dealer_sold_out') });
      else {
        const affordable = this.s.money >= VEHICLE_PRICE[offer];
        candidates.push({ target: DEALER,
          prompt: affordable ? { id: `prompt_buy_${offer}`, button: true } : { id: `notice_${offer}_price`, button: false },
          run: () => affordable ? this.buyVehicle(offer) : this.say('dealer_no_money') });
      }
    }
    let best: Action | null = null, bestDistance = INTERACT_RADIUS;
    for (const c of candidates) {
      const d = distance(frame.position, c.target.point);
      if (d <= bestDistance) { best = c; bestDistance = d; }
    }
    return best;
  }

  private buyLicence() {
    this.s.money -= LICENCE_PRICE;
    this.s.licence = true;
    this.s.phase = 'at_pizzeria';
    this.say('market_sold');
    this.persist();
  }

  private buyVehicle(vehicle: BoughtVehicle) {
    this.s.money -= VEHICLE_PRICE[vehicle];
    this.s.vehicle = vehicle;
    this.say(vehicle === 'car' ? 'dealer_sold_car' : 'dealer_sold_bicycle');
    this.persist();
  }

  private pickUp() {
    const d = this.currentDelivery;
    const path = this.options.router.route(PIZZERIA.point, d.point);
    const metres = path ? this.options.router.distance(path) : distance(PIZZERIA.point, d.point);
    this.s.par = parSeconds(metres, this.s.vehicle);
    this.s.elapsed = 0;
    this.s.phase = 'carrying';
    this.say(d.handover);
    this.persist();
  }

  private handOver() {
    const d = this.currentDelivery;
    const t = tip(this.s.elapsed, this.s.par);
    this.s.money += BASE_PAY + t;
    this.s.phase = 'delivered';
    this.s.delivery += 1;
    this.s.elapsed = this.s.par = 0;
    this.lastResult = 'delivered';
    this.say(t >= 5 ? d.customer.fast : t >= 1 ? d.customer.ok : d.customer.slow, `paid_tip_${t}`);
    this.persist();
  }

  private fail() {
    this.s.phase = 'failed';
    this.s.delivery += 1;
    this.s.elapsed = this.s.par = 0;
    this.lastResult = 'failed';
    this.say('delivery_failed');
    this.persist();
  }

  private greet(position: WorldPoint) {
    const lines: [RouteTarget, string | null][] = [
      [MARKET, this.s.phase === 'licence_needed' ? 'market_offer' : 'market_done'],
      [PIZZERIA, this.s.phase === 'licence_needed' ? 'pizzeria_no_licence' : this.s.phase === 'carrying' ? null
        : this.lastResult === 'failed' ? 'pizzeria_after_fail' : this.lastResult === 'delivered' ? 'pizzeria_back'
          : this.s.delivery === 0 ? 'pizzeria_first' : null],
      [DEALER, this.s.vehicle === 'car' ? null : 'dealer_greet'],
    ];
    for (const [target, line] of lines) {
      const d = distance(position, target.point);
      if (d > GREET_REARM) this.greeted.delete(target.id);
      else if (d < GREET_RADIUS && line && !this.greeted.has(target.id) && this.queue.length === 0) {
        this.greeted.add(target.id);
        this.say(line);
      }
    }
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
    const action = this.frame ? this.action(this.frame) : null;
    if (action?.prompt) {
      return action.prompt.button ? `${action.prompt.id}_${this.frame!.input === 'gamepad' ? 'pad' : 'key'}` : action.prompt.id;
    }
    switch (this.s.phase) {
      case 'idle': return null;
      case 'licence_needed': return 'hint_licence';
      case 'carrying': return this.currentDelivery.hint;
      default: return 'hint_pizzeria';
    }
  }

  private buildState(): MissionHudState {
    const targets: RouteTarget[] = [PIZZERIA, DEALER];
    if (!this.s.licence) targets.push(MARKET);
    if (this.s.phase === 'carrying') targets.push(this.dropoffTarget());
    const active = this.s.phase === 'idle' ? null : this.s.phase === 'licence_needed' ? MARKET.id
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
