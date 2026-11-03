// The mission save text (format 4) inside a save slot (core/save.ts keeps it opaque) and migrations of formats
// 1–3 (docs/design/story.md, "Mission state machine").
import type { DeliveryJob, VehicleOwnership } from '../economy';
import { PERMIT_DELIVERIES, START_MONEY } from '../economy';
import { DELIVERIES } from '../content/deliveries';
import { PHRASES, type PhraseCounts } from '../content/announcements';

export type Phase = 'idle' | 'permit_needed' | 'at_pizzeria' | 'carrying' | 'delivered' | 'failed';

/** Slot details of a mission save; an invalid text reads as a new game. */
export function missionSummary(text: string | null) {
  const s = parseSave(text, 0);
  return { deliveries: s.delivery, money: s.money, devMoney: s.devMoney, lastAddress: s.lastAddress };
}

/** The pizzeria's LIMITED TIME offer clock (game seconds; missions/orders.ts drawLimitedOrder). */
export interface LimitedOffer {
  /** Offers drawn so far: the next draw's number. */
  n: number;
  /** Pool ids of the offer on the menu; [] during the cooldown. */
  stops: string[];
  /** Seconds left of the offer on the menu, or of the cooldown before the next draw (0: draw when due). */
  left: number;
}

export interface MissionSave {
  version: 4;
  job: DeliveryJob;
  /** Completed short orders, including cancellations; ten finish one rented round. */
  shortDeliveries: number;
  longDeliveries: number;
  longAnnounced: boolean;
  phase: Phase;
  money: number;
  /** Delivery orders left on the rented courier permit; taking an order uses one. */
  permit: number;
  plates: boolean;
  kidney: boolean;
  /** The permit expiry warning was bought back. */
  warning: boolean;
  vehicle: VehicleOwnership;
  /** Orders served (delivered or cancelled). The first DELIVERIES.length short orders are the scripted opening, in a
   *  seeded order (missions/orders.ts openingOrder); later ones come from the address pool (or repeat the opening when
   *  the build has no pool). */
  delivery: number;
  /** Scripted opening ids served (delivered or cancelled), so a continued game never repeats one. Saves from before
   *  the seeded opening played DELIVERIES in list order: they read as its first `shortDeliveries` entries. */
  opening: string[];
  /** Pizzas handed over. */
  pizzas: number;
  /** Seeds the order draws and line picks of this game. */
  seed: number;
  /** Pool address ids served (delivered or cancelled) since the pool last started over. */
  delivered: string[];
  /** Stop ids of the quoted or carried order: `delivery_<n>` for the scripted ones, pool ids otherwise; [] = none drawn. */
  order: string[];
  /** Address of the last finished order's last drop-off, or null before the first (the slot label). */
  lastAddress: string | null;
  /** Index in `order` of the next drop-off. */
  stop: number;
  /** Seconds since pickup and the par time of the whole order being carried. */
  elapsed: number;
  par: number;
  /** Par seconds to the first drop-off of a double order (0 for a single). */
  firstPar: number;
  /** Euros the developer tools added to `money` (localhost only, ui/devMenu.ts), so no scoreboard counts them.
   *  Missing in saves from before the tools: 0. */
  devMoney: number;
  /** Number of draws from each spoken phrase pool. */
  phrases?: PhraseCounts;
  /** Missing in saves from before LIMITED TIME offers: the first offer is due once long jobs are unlocked. */
  limited: LimitedOffer;
  /** The carried order has been ridden on the pizzeria's loaner scooter, so its pay loses LOANER_FEE. Missing: false. */
  loaner?: boolean;
  /** Euros still owed for permits rented on credit (economy.ts debtRepayment). Missing: 0. */
  debt?: number;
  /** The black market's truck heist: the trophy truck waits in the yard (`pickup`) or is being driven to the bridge
   *  (`deliver`). Missing or null: no job running. A reload puts a running job back to `pickup` with the truck in the
   *  yard. */
  heist?: HeistStage | null;
}

export type HeistStage = 'pickup' | 'deliver';

/** The save format of main e323e39 (four looping deliveries, no address pool). */
interface MissionSaveV2 {
  version: 2;
  phase: Phase;
  money: number;
  permit: number;
  plates: boolean;
  kidney: boolean;
  warning: boolean;
  vehicle: VehicleOwnership;
  delivery: number;
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

const VALID_PHASES: readonly Phase[] = ['idle', 'permit_needed', 'at_pizzeria', 'carrying', 'delivered', 'failed'];
const VALID_VEHICLES: readonly VehicleOwnership[] = ['none', 'scooter', 'car', 'truck'];

export const newSeed = () => Math.floor(Math.random() * 2 ** 31);

const freshOffer = (): LimitedOffer => ({ n: 0, stops: [], left: 0 });

export function freshSave(seed: number): MissionSave {
  return { version: 4, job: 'short', shortDeliveries: 0, longDeliveries: 0, longAnnounced: false, phase: 'idle', money: START_MONEY, permit: 0, plates: false, kidney: false, warning: false,
    vehicle: 'none', delivery: 0, opening: [], pizzas: 0, seed, delivered: [], order: [], lastAddress: null, stop: 0, elapsed: 0,
    par: 0, firstPar: 0, devMoney: 0, phrases: {}, limited: freshOffer(), loaner: false, debt: 0 };
}

/** The opening served by a save from before the seeded opening order: DELIVERIES in list order. */
const listOpening = (served: number) => DELIVERIES.slice(0, Math.min(served, DELIVERIES.length)).map(d => d.id);
const SCRIPTED_STOPS: ReadonlySet<string> = new Set(DELIVERIES.map(d => d.id));

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const bool = (v: unknown): v is boolean => typeof v === 'boolean';
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(id => typeof id === 'string');

/** A v1 save (main 2673ec0): an owned licence becomes one fresh permit rental. */
function migrateV1(s: Partial<MissionSaveV1>): MissionSaveV2 | null {
  const phases = ['idle', 'licence_needed', 'at_pizzeria', 'carrying', 'delivered', 'failed'];
  if (!phases.includes(s.phase as string) || !num(s.money) || !bool(s.licence)
    || !VALID_VEHICLES.includes(s.vehicle as VehicleOwnership) || !num(s.delivery) || !num(s.elapsed) || !num(s.par)) {
    return null;
  }
  const phase: Phase = s.phase === 'licence_needed' ? 'permit_needed' : s.phase as Phase;
  return { version: 2, phase, money: s.money, permit: s.licence ? PERMIT_DELIVERIES : 0, plates: false, kidney: false,
    warning: false, vehicle: s.vehicle as VehicleOwnership, delivery: s.delivery, elapsed: s.elapsed, par: s.par };
}

function validV2(s: Partial<MissionSaveV2>): MissionSaveV2 | null {
  if (!VALID_PHASES.includes(s.phase as Phase) || !num(s.money) || !num(s.permit) || !bool(s.plates) || !bool(s.kidney)
    || !bool(s.warning) || !VALID_VEHICLES.includes(s.vehicle as VehicleOwnership) || !num(s.delivery)
    || !num(s.elapsed) || !num(s.par)) {
    return null;
  }
  return s as MissionSaveV2;
}

/** A v2 save (four looping deliveries): its delivery count carries over, a carried scripted order stays carried,
 *  and the address pool starts unused. */
function migrateV2(s: MissionSaveV2, seed: number): MissionSave {
  const delivery = Math.floor(s.delivery);
  return { ...s, version: 4, job: 'short', shortDeliveries: delivery, longDeliveries: 0, longAnnounced: false, delivery, pizzas: delivery, seed, delivered: [], stop: 0, firstPar: 0, devMoney: 0,
    order: s.phase === 'carrying' ? [DELIVERIES[delivery % DELIVERIES.length].id] : [], opening: listOpening(delivery),
    lastAddress: delivery > 0 ? DELIVERIES[(delivery - 1) % DELIVERIES.length].address : null, phrases: {},
    limited: freshOffer() };
}

function validV3(s: Partial<MissionSave>): MissionSave | null {
  const v2 = validV2({ ...s, version: 2 } as Partial<MissionSaveV2>);
  if (!v2 || !num(s.pizzas) || !num(s.seed) || !ids(s.delivered) || !ids(s.order) || !num(s.stop)
    || !num(s.firstPar) || !(s.lastAddress === null || typeof s.lastAddress === 'string')
    || !(s.devMoney === undefined || num(s.devMoney))) {
    return null;
  }
  return s as MissionSave;
}

export function parseSave(raw: string | null, seed: number): MissionSave {
  if (!raw) return freshSave(seed);
  try {
    const parsed = JSON.parse(raw) as { version?: unknown; vehicle?: unknown };
    // The dealer's bicycle (a speed-capped car) became the scooter; every save version stores the same value.
    if (parsed?.vehicle === 'bicycle') parsed.vehicle = 'scooter';
    const v2 = parsed?.version === 1 ? migrateV1(parsed as Partial<MissionSaveV1>)
      : parsed?.version === 2 ? validV2(parsed as Partial<MissionSaveV2>) : null;
    const save = v2 ? migrateV2(v2, seed) : (parsed?.version === 3 || parsed?.version === 4) ? validV3(parsed as Partial<MissionSave>) : null;
    if (!save) return freshSave(seed);
    if (parsed.version !== 4) Object.assign(save, { version: 4, job: 'short', shortDeliveries: save.delivery,
      longDeliveries: 0, longAnnounced: false });
    if (!['short', 'long', 'limited'].includes(save.job) || !num(save.shortDeliveries) || !num(save.longDeliveries)
      || !bool(save.longAnnounced)) return freshSave(seed);
    save.opening = ids(save.opening) ? [...new Set(save.opening.filter(id => SCRIPTED_STOPS.has(id)))]
      : listOpening(Math.floor(save.shortDeliveries));
    const offer = save.limited as Partial<LimitedOffer> | undefined;
    save.limited = offer && num(offer.n) && ids(offer.stops) && num(offer.left)
      ? { n: Math.floor(offer.n), stops: offer.stops, left: offer.left } : freshOffer();
    save.heist = save.heist === 'pickup' || save.heist === 'deliver' ? 'pickup' : null;
    // A finished delivery's result line is not replayed after a reload: the next step is the pizzeria.
    if (save.phase === 'delivered' || save.phase === 'failed' || (save.phase === 'carrying' && save.par <= 0)) {
      save.phase = save.permit > 0 ? 'at_pizzeria' : 'permit_needed';
    }
    const phrases = Object.fromEntries(Object.entries(save.phrases ?? {})
      .filter(([kind, count]) => kind in PHRASES && num(count))
      .map(([kind, count]) => [kind, Math.floor(count as number)]));
    return { ...save, money: Math.round(save.money), permit: Math.floor(save.permit), delivery: Math.floor(save.delivery),
      pizzas: Math.floor(save.pizzas), stop: Math.floor(save.stop), devMoney: Math.round(save.devMoney ?? 0),
      phrases, loaner: save.phase === 'carrying' && save.loaner === true, debt: num(save.debt) ? Math.round(save.debt) : 0 };
  } catch {
    return freshSave(seed);
  }
}
