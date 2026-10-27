// The mission save text (format 3) inside a save slot (core/save.ts keeps it opaque) and the migrations of formats
// 1 and 2 (docs/design/story.md, "Mission state machine").
import type { VehicleOwnership } from '../economy';
import { PERMIT_DELIVERIES, START_MONEY } from '../economy';
import { DELIVERIES } from '../content/deliveries';

export type Phase = 'idle' | 'permit_needed' | 'at_pizzeria' | 'carrying' | 'delivered' | 'failed';

export interface MissionSave {
  version: 3;
  phase: Phase;
  money: number;
  /** Delivery orders left on the rented courier permit; taking an order uses one. */
  permit: number;
  plates: boolean;
  kidney: boolean;
  /** The permit expiry warning was bought back. */
  warning: boolean;
  vehicle: VehicleOwnership;
  /** Orders served (delivered or cancelled). The first DELIVERIES.length are the scripted opening; later orders
   *  come from the address pool (or repeat DELIVERIES when the build has no pool). */
  delivery: number;
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
}

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
const VALID_VEHICLES: readonly VehicleOwnership[] = ['none', 'scooter', 'car'];

export const newSeed = () => Math.floor(Math.random() * 2 ** 31);

export function freshSave(seed: number): MissionSave {
  return { version: 3, phase: 'idle', money: START_MONEY, permit: 0, plates: false, kidney: false, warning: false,
    vehicle: 'none', delivery: 0, pizzas: 0, seed, delivered: [], order: [], lastAddress: null, stop: 0, elapsed: 0,
    par: 0, firstPar: 0, devMoney: 0 };
}

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
  return { ...s, version: 3, delivery, pizzas: delivery, seed, delivered: [], stop: 0, firstPar: 0, devMoney: 0,
    order: s.phase === 'carrying' ? [DELIVERIES[delivery % DELIVERIES.length].id] : [],
    lastAddress: delivery > 0 ? DELIVERIES[(delivery - 1) % DELIVERIES.length].address : null };
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
    const save = v2 ? migrateV2(v2, seed) : parsed?.version === 3 ? validV3(parsed as Partial<MissionSave>) : null;
    if (!save) return freshSave(seed);
    // A finished delivery's result line is not replayed after a reload: the next step is the pizzeria.
    if (save.phase === 'delivered' || save.phase === 'failed' || (save.phase === 'carrying' && save.par <= 0)) {
      save.phase = save.permit > 0 ? 'at_pizzeria' : 'permit_needed';
    }
    return { ...save, money: Math.round(save.money), permit: Math.floor(save.permit), delivery: Math.floor(save.delivery),
      pizzas: Math.floor(save.pizzas), stop: Math.floor(save.stop), devMoney: Math.round(save.devMoney ?? 0) };
  } catch {
    return freshSave(seed);
  }
}
