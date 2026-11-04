// Travel between cities (docs/cities.md). A city is a page (core/city.ts), so a trip is a page load: the leaving page
// writes a ticket to this tab's session storage and opens the target city's URL; the arriving page takes the ticket,
// skips the start menu and places the player at the arrival point, in their vehicle when they drove through.
//
// Money, permits, the owned vehicle and a running order are the mission save (MissionSystem.serialize), which the
// ticket carries: it is client-side state in solo and in multiplayer alike (the server keeps only each room's hourly
// scoreboard), so a trip keeps it in both. Multiplayer: the ticket remembers that the player was in a room, and the
// arriving page joins the target city's room (net/config.ts multiplayerUrl) at the arrival point.
import { CITIES, CITY, HOME_CITY, isCityName, isHome, type CityName, type CityProfile } from '../core/city';
import type { KeyValueStore } from '../core/save';
import { EMPTY_HUD_STATE, type MissionHudSource } from './contract';
import type { VehicleKind } from '../physics/constants';

export const TICKET_KEY = 'turku3d.travel.v1';
/** The Turku portal the player last came through, kept for the stay in the other city (the return lands there). */
export const CAME_THROUGH_KEY = 'turku3d.travel.portal.v1';
/** A ticket older than this is a stale tab restore, not a trip. */
export const TICKET_TTL_MS = 120_000;

export interface TravelTicket {
  v: 1;
  from: CityName;
  to: CityName;
  /** The Turku portal of the trip (left through, or returned to). */
  portal: string | null;
  /** The vehicle the player drove through in, or null on foot. */
  ride: { id: string; kind: VehicleKind } | null;
  /** MissionSystem.serialize() at departure. */
  mission: string;
  multiplayer: boolean;
  /** Unix milliseconds of departure. */
  at: number;
}

const KINDS: readonly VehicleKind[] = ['car', 'scooter', 'truck'];

function validTicket(v: unknown): TravelTicket | null {
  const t = v as Partial<TravelTicket> | null;
  if (!t || t.v !== 1 || !isCityName(t.from) || !isCityName(t.to) || typeof t.mission !== 'string'
    || typeof t.multiplayer !== 'boolean' || typeof t.at !== 'number' || !Number.isFinite(t.at)) return null;
  if (t.portal !== null && typeof t.portal !== 'string') return null;
  if (t.ride !== null && !(t.ride && typeof t.ride.id === 'string' && KINDS.includes(t.ride.kind))) return null;
  return t as TravelTicket;
}

export function writeTicket(store: KeyValueStore | null, ticket: TravelTicket) {
  try { store?.setItem(TICKET_KEY, JSON.stringify(ticket)); } catch { /* blocked storage: the trip arrives fresh */ }
}

/** Takes (and removes) a fresh ticket to `city`. A ticket to another city, or an old one, is dropped. */
export function takeTicket(store: KeyValueStore | null, city: CityName, now: number): TravelTicket | null {
  let ticket: TravelTicket | null = null;
  try {
    ticket = validTicket(JSON.parse(store?.getItem(TICKET_KEY) ?? 'null'));
    store?.removeItem(TICKET_KEY);
  } catch { return null; }
  if (!ticket || ticket.to !== city || now - ticket.at > TICKET_TTL_MS || now < ticket.at - 5000) return null;
  // The stay remembers the portal it started from; coming home ends it.
  try {
    if (ticket.to === HOME_CITY) store?.removeItem(CAME_THROUGH_KEY);
    else if (ticket.portal) store?.setItem(CAME_THROUGH_KEY, ticket.portal);
  } catch { /* the return falls back to the city's first portal */ }
  return ticket;
}

/** The Turku portal this stay started from, if this tab came through one. */
export function cameThrough(store: KeyValueStore | null): string | null {
  try { return store?.getItem(CAME_THROUGH_KEY) ?? null; } catch { return null; }
}

/** Whether `city`'s tiles are served on this origin: its tile mount answers health with state "ready". Turku always is.
 *  `get` is the JSON fetch (tests pass their own). */
export async function cityServed(city: CityProfile, get: (url: string) => Promise<unknown> = defaultGet): Promise<boolean> {
  if (isHome(city)) return true;
  try {
    const health = await get(`${city.tiles}health`) as { state?: unknown } | null;
    return health?.state === 'ready';
  } catch { return false; }
}

const defaultGet = async (url: string) => {
  const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(4000) });
  return res.ok && (res.headers.get('content-type') ?? '').includes('json') ? res.json() : null;
};

/** Availability of the portal targets, re-checked at most every `ttlMs` while asked for. */
export class CityAvailability {
  private known = new Map<CityName, { served: boolean; at: number }>();
  private pending = new Set<CityName>();

  constructor(private readonly check: (city: CityProfile) => Promise<boolean> = cityServed, private readonly ttlMs = 30_000) {}

  /** The last known state (null before the first answer); starts a check when it is older than the TTL. */
  served(name: CityName, now: number): boolean | null {
    const k = this.known.get(name);
    if ((!k || now - k.at > this.ttlMs) && !this.pending.has(name)) {
      this.pending.add(name);
      void this.check(CITIES[name]).then(served => { this.known.set(name, { served, at: now }); })
        .finally(() => this.pending.delete(name));
    }
    return k?.served ?? null;
  }

  /** Waits for a fresh answer (the moment the player enters a portal). */
  async confirm(name: CityName, now: number): Promise<boolean> {
    const served = await this.check(CITIES[name]);
    this.known.set(name, { served, at: now });
    return served;
  }
}

/** A short full-screen transition while the next city's page loads. */
export function showTransition(parent: HTMLElement, city: CityProfile): HTMLElement {
  const el = document.createElement('div');
  el.id = 'travel-transition';
  el.setAttribute('role', 'status');
  const title = document.createElement('h2');
  title.textContent = `Travelling to ${city.title}`;
  const where = document.createElement('p');
  where.textContent = isHome(city) ? 'Back to Turku' : city.spawn.label;
  el.append(title, where);
  parent.append(el);
  requestAnimationFrame(() => el.classList.add('shown'));
  return el;
}

/** The boot screen of an arriving page names the trip (index.html #loading-screen). */
export function announceArrival(store: KeyValueStore | null, doc: Document = document) {
  let to: string | null = null;
  try { to = (JSON.parse(store?.getItem(TICKET_KEY) ?? 'null') as { to?: string } | null)?.to ?? null; } catch { /* none */ }
  if (!isHome()) doc.title = `Turku3D · ${CITY.title}`;
  const h1 = doc.querySelector('#loading-screen h1');
  if (!h1) return;
  if (to === CITY.name) h1.textContent = `Travelling to ${CITY.title}`;
  else if (!isHome()) h1.textContent = `Bringing ${CITY.title} to life`;
}

/** The mission HUD outside Turku: the wallet only. The missions are Turku content; their state waits for the return. */
export const walletOnly = (missions: MissionHudSource): MissionHudSource => ({
  subscribe: listener => missions.subscribe(state => listener({ ...EMPTY_HUD_STATE, money: state.money })),
});
