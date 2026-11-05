// City portals (docs/cities.md): drive or walk through a glowing arch under a Turku bridge to play another city, and
// through the return arch at that city's centre (or the pause menu) to come back. Pure data and geometry; the arches
// are drawn by portalMarkers.ts, the trip itself is gameplay/travel.ts. Portals are fixed world data, the same on every
// client; only the player who passes one travels (multiplayer: the trip leaves this city's room for the target's).
import { CITIES, CITY, HOME_CITY, type CityName, type CityProfile } from '../core/city';
import { headingToYaw } from '../core/origin';

export interface PortalDef {
  id: string;
  /** The city this portal takes the player to. */
  to: CityName;
  /** Where it stands, for the map label and the report: the bridge and the lanes under it. */
  place: string;
  /** Centre of the trigger, engine metres of the city it stands in. */
  x: number;
  z: number;
  /** Grid bearing (deg, clockwise from grid north) of travel through the portal: the lanes' driving direction. */
  headingDeg: number;
  /** Trigger width across the lanes and depth along them (m). */
  width: number;
  depth: number;
  /** Ground probe: a downward ray from this height (engine y) finds the road under the bridge deck, not the deck.
   *  Null: from the sky (an open square). */
  rayTop: number | null;
}

/** The trigger reaches this far above the ground under its centre, so traffic on the bridge deck above never travels. */
export const PORTAL_HEIGHT = 4.5;
/** And this far below it (a sloped carriageway, a jump). */
const PORTAL_BELOW = 1.5;
/** A portal arms only once the player has been at least this far outside it (an arrival or a load inside one). */
const ARM_MARGIN_M = 2;
/** Where a return through a Turku portal places the player: this far past the portal, facing on along the lanes, clear
 *  of the deck (its far edge is 16-19 m from the centre), so the start's ground ray finds the road, not the bridge. */
export const EXIT_DISTANCE_M = 24;

/**
 * Turku's portals: one per lane group under two bridges (OSM 2026-10-24, roads.json v1 of tiles t0_7_-1 and t0_0_-4).
 * Kupittaansilta, the Tykistökatu/Kalevantie bridge at Kupittaa station (OSM man_made=bridge 526095387, bridge ways
 * 4250695/45191356), crosses Helsinginkatu and the Rantarata: both of its carriageways go to Espoo. Aninkaistensilta
 * (road 9, OSM 514028775, bridge ways 4265032/4265033) crosses Ratapihankatu: the south-west lanes beside the railway
 * yard go to Amsterdam, the north-east lanes beside the pedestrian and cycle path go to Rotterdam. Each centre is on
 * the carriageway's centreline midway between the outer edges of the deck (its outermost bridge ways).
 */
export const TURKU_PORTALS: readonly PortalDef[] = [
  { id: 'kupittaa-southeast', to: 'espoo', place: 'Kupittaansilta (Kalevan silta), Helsinginkatu south-east lanes',
    x: 1772.1, z: -67.9, headingDeg: 148, width: 15, depth: 6, rayTop: 23.5 },
  { id: 'kupittaa-northwest', to: 'espoo', place: 'Kupittaansilta (Kalevan silta), Helsinginkatu north-west lanes',
    x: 1787.2, z: -75.0, headingDeg: 325.5, width: 12, depth: 6, rayTop: 23.5 },
  { id: 'aninkainen-rail', to: 'amsterdam', place: 'Aninkaistensilta, Ratapihankatu lanes on the railway side',
    x: 220.1, z: -912.4, headingDeg: 237.4, width: 9, depth: 6, rayTop: 13.5 },
  { id: 'aninkainen-path', to: 'rotterdam', place: 'Aninkaistensilta, Ratapihankatu lanes on the pedestrian side',
    x: 228.6, z: -899.1, headingDeg: 57, width: 15.5, depth: 6, rayTop: 13.5 },
];

/** The walk-in return arch of a city other than Turku: this far ahead of the centre spawn, facing the arriving player. */
const RETURN_AHEAD_M = 12;

/** The return arch at `city`'s centre (none in Turku). */
export function returnPortal(city: CityProfile): PortalDef | null {
  if (city.name === HOME_CITY) return null;
  // Engine metres of `city` itself, whichever city this page plays.
  const s = { x: city.spawn.E - city.origin.E, z: -(city.spawn.N - city.origin.N) };
  const h = city.spawn.headingDeg * Math.PI / 180;
  return { id: `${city.name}-return`, to: HOME_CITY, place: `${city.spawn.label}, ${city.title}`,
    x: s.x + RETURN_AHEAD_M * Math.sin(h), z: s.z - RETURN_AHEAD_M * Math.cos(h), headingDeg: city.spawn.headingDeg,
    width: 7, depth: 3, rayTop: null };
}

/** The portals standing in `city`. */
export function portalsIn(city: CityProfile = CITY): readonly PortalDef[] {
  if (city.name === HOME_CITY) return TURKU_PORTALS;
  const back = returnPortal(city);
  return back ? [back] : [];
}

/** `(x, z)` in the portal's frame: `along` the travel direction from its centre, `across` to the right of it. */
export function portalLocal(p: PortalDef, x: number, z: number): { along: number; across: number } {
  const h = p.headingDeg * Math.PI / 180, fx = Math.sin(h), fz = -Math.cos(h);
  const dx = x - p.x, dz = z - p.z;
  return { along: dx * fx + dz * fz, across: dx * -fz + dz * fx };
}

/** Whether a body at engine `(x, y, z)` is inside the portal standing on ground `groundY`. */
export function insidePortal(p: PortalDef, groundY: number, x: number, y: number, z: number, margin = 0): boolean {
  const { along, across } = portalLocal(p, x, z);
  return Math.abs(along) <= p.depth / 2 + margin && Math.abs(across) <= p.width / 2 + margin
    && y >= groundY - PORTAL_BELOW - margin && y <= groundY + PORTAL_HEIGHT + margin;
}

/** The Turku portal a player returns through from `city`: the one they came through, else the first one to it. */
export function returnTarget(city: CityName, cameThrough: string | null): PortalDef {
  const used = TURKU_PORTALS.find(p => p.id === cameThrough && p.to === city);
  const portal = used ?? TURKU_PORTALS.find(p => p.to === city);
  if (!portal) throw new Error(`no Turku portal leads to ${city}`);
  return portal;
}

/** Where a return through Turku `portal` lands (Turku E/N and heading, computed in Turku's frame on any city's page):
 *  past the portal on its lanes, facing away from it. */
export function exitPose(portal: PortalDef): { E: number; N: number; headingDeg: number } {
  const h = portal.headingDeg * Math.PI / 180, o = CITIES[HOME_CITY].origin;
  return { E: o.E + portal.x + EXIT_DISTANCE_M * Math.sin(h), N: o.N - (portal.z - EXIT_DISTANCE_M * Math.cos(h)),
    headingDeg: portal.headingDeg };
}

/** Engine yaw of a vehicle chassis (+X forward) driving along grid bearing `headingDeg`. */
export const chassisYaw = (headingDeg: number) => headingToYaw(headingDeg) + Math.PI / 2;

/** The target city of a portal, for its label. */
export const portalCity = (p: PortalDef) => CITIES[p.to];

/**
 * Which portal the player passes this frame. A portal fires once when the player enters it; it arms only after the
 * player has been seen clearly outside it, so arriving or loading a game inside one never travels at once.
 */
export class PortalTriggers {
  private armed = new Map<string, boolean>();

  constructor(private readonly portals: readonly PortalDef[]) {}

  /** `ground(id)`: the portal's ground height, or null while unknown (its collision is not resident yet). */
  update(x: number, y: number, z: number, ground: (id: string) => number | null): PortalDef | null {
    let hit: PortalDef | null = null;
    for (const p of this.portals) {
      const g = ground(p.id);
      if (g === null) continue;
      if (!insidePortal(p, g, x, y, z, ARM_MARGIN_M)) { this.armed.set(p.id, true); continue; }
      if (!this.armed.get(p.id) || !insidePortal(p, g, x, y, z)) continue;
      this.armed.set(p.id, false);
      hit ??= p;
    }
    return hit;
  }

  /** Disarms every portal: the next trip needs the player to leave the one they stand in first. */
  reset() { this.armed.clear(); }
}
