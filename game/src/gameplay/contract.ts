// Shared contract between the mission system (gameplay-missions) and the minimap/router
// (gameplay-map-route). Coordinates are local metres in the game's world frame (core/origin.ts),
// x east, z south (three.js: +z toward the viewer at the origin), y up.

export interface WorldPoint {
  x: number;
  z: number;
  /** Surface height when a route follows a bridge; absent on unsurveyed streets. */
  y?: number;
}

/** `job`: a black-market job's place (the truck heist's yard and drop-off), probed from ZONE_RAY_TOP, not checked as a
 *  delivery drop-off. */
export type TargetKind = 'pickup' | 'dropoff' | 'shop' | 'market' | 'job';

export interface RouteTarget {
  id: string;
  point: WorldPoint;
  label: string;
  kind: TargetKind;
  /** Where the street route ends when the marker lies off the street graph or under a bridge; default `point`. */
  approach?: WorldPoint;
}

export interface MissionHudState {
  /** Every marker the minimap draws. */
  targets: RouteTarget[];
  /** The target the route is drawn to, or null for no route. */
  activeTargetId: string | null;
  money: number;
  /** Seconds left for the current delivery, or null when no timer runs. */
  timerSeconds: number | null;
  /** One short line of Finnish text shown above the minimap, or null. */
  message: string | null;
}

export type HudStateListener = (state: MissionHudState) => void;

export interface MissionHudSource {
  /** Calls the listener immediately with the current state and on every change. Returns unsubscribe. */
  subscribe(listener: HudStateListener): () => void;
}

export interface Router {
  /** Shortest walkable/drivable path along the street network, from the nearest graph point to `from`
   *  to the nearest graph point to `to`, including both endpoints. Null when either end is off the graph.
   *  `mode` picks walking or driving; by default the router's current travel mode (the player's). */
  route(from: WorldPoint, to: WorldPoint, mode?: 'foot' | 'car'): WorldPoint[] | null;
  /** Path length in metres. */
  distance(path: WorldPoint[]): number;
}

export const EMPTY_HUD_STATE: MissionHudState = {
  targets: [],
  activeTargetId: null,
  money: 0,
  timerSeconds: null,
  message: null,
};

export const emptyHudSource: MissionHudSource = {
  subscribe(listener) {
    listener(EMPTY_HUD_STATE);
    return () => {};
  },
};
