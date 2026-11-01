// Ambient traffic density: how many of the conveyor's slots carry a car, per street and clock hour.
//
// A lane's flow (vehicles per hour per lane at the busiest hour) comes from its street class and its area; the
// clock hour scales it by a weekday profile. The share of slots that carry a car is that flow over the conveyor's
// capacity (one slot per headway, 600 per hour). Which slots are filled is a hash of the car's identity against
// that share (TrafficNetwork.present), so it is the same on every client.
//
// Sources (derived aggregates only; no raw counts are stored):
// - HOURLY: Fintraffic / Digitraffic TMS (LAM) raw per-vehicle data, licence CC BY 4.0 ("Source: Fintraffic /
//   digitraffic.fi"). The eight stations within 10 km of Turku centre (205 vt8 Raisio, 233 kt40 Hauninen,
//   234 kt40 Oriketo, 237 vt1 Kurkela, 249 vt8 Ihala, 251 kt40 Krookila, 252 st185 Artukainen,
//   257 yt1851 Härkämäki), Tuesday to Thursday 6–8 and 13–15 October 2026, local time: the mean share of each
//   station-day's traffic per hour, relative to the busiest hour (15–16). 48 station-days.
// - PEAK_FLOW: the same stations carry 450–1,100 vehicles per lane in their busiest hour (two to six lanes, outside
//   the centre). Signalised city-centre streets carry less per lane; the street classes below scale down from
//   there to residential streets and living streets, where a car is a rare access trip.
import { TRAFFIC } from './constants';

/** Relative traffic per clock hour (index h covers h:00–h+1:00), busiest hour = 1. Weekday LAM profile. */
export const HOURLY: readonly number[] = [
  0.04, 0.02, 0.02, 0.02, 0.04, 0.23, 0.63, 0.75, 0.65, 0.51, 0.51, 0.57,
  0.60, 0.61, 0.71, 1.00, 0.88, 0.69, 0.57, 0.44, 0.35, 0.20, 0.12, 0.06,
];

/** Vehicles per hour per lane at the busiest hour by OSM `highway` (links as their road). Service roads, parking
 *  aisles and driveways carry no ambient traffic; pedestrian streets have no car lanes at all. */
export const PEAK_FLOW: Readonly<Record<string, number>> = {
  motorway: 600, trunk: 540, primary: 480, secondary: 420, tertiary: 240, unclassified: 120, residential: 45,
  living_street: 6, road: 45,
};
/** roads.json v1 has only a 2-bit class (path, service, street, major): "street" lumps tertiary, unclassified,
 *  residential and living streets (by length mostly residential), "major" primary to motorway. */
export const V1_PEAK_FLOW: readonly number[] = [0, 0, 60, 450];

/** Turku market square (Kauppatori) in engine x, z: the centre of the area rule. */
const CENTRE = [180, -70] as const;
/** Minor streets (below secondary) carry this much more within `CORE` m of the centre, and `OUTER` beyond `EDGE`
 *  m: shops, deliveries and parking search in the centre, quiet residential districts outside. Arterials keep
 *  their class flow everywhere. */
const AREA = { core: 900, edge: 3000, centre: 1.3, outer: 0.7 } as const;

/** Area factor at (x, z) for a street of the given peak flow. */
export function areaFactor(x: number, z: number, peak: number): number {
  if (peak >= PEAK_FLOW.secondary) return 1;
  const d = Math.hypot(x - CENTRE[0], z - CENTRE[1]);
  if (d <= AREA.core) return AREA.centre;
  if (d >= AREA.edge) return AREA.outer;
  return AREA.centre + (AREA.outer - AREA.centre) * (d - AREA.core) / (AREA.edge - AREA.core);
}

/** Peak flow (vehicles per hour per lane) of a street: its class times the area factor at its midpoint. */
export function streetFlow(highway: string | null, v1Class: number, x: number, z: number): number {
  const base = highway === null ? V1_PEAK_FLOW[v1Class] ?? 0 : PEAK_FLOW[highway.replace(/_link$/, '')] ?? 0;
  return base * areaFactor(x, z, base);
}

/** Relative traffic at a clock hour (0 <= hour < 24): the hourly values sit at the middle of their hour and are
 *  interpolated linearly, so the curve has no steps. */
export function profile(hour: number): number {
  const h = ((hour - 0.5) % 24 + 24) % 24, i = Math.floor(h), f = h - i;
  return HOURLY[i] + (HOURLY[(i + 1) % 24] - HOURLY[i]) * f;
}

/** Slots per hour on one lane: the conveyor's capacity. */
export const LANE_CAPACITY = 3600 / TRAFFIC.headway;

/** Share of a lane's slots that carry a car: flow × profile × multiplier over the capacity, at most 1. */
export function slotShare(flow: number, hour: number, multiplier = 1): number {
  return Math.min(1, Math.max(0, flow * profile(hour) * multiplier / LANE_CAPACITY));
}

/** The clock hour a car's birth time (traffic seconds) falls on, and the global density multiplier. */
export interface TrafficDensity {
  hourAt(t: number): number;
  multiplier: number;
}
