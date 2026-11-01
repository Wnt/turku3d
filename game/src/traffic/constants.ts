// Ambient traffic tuning shared by the physics worker (simulation) and the main thread (rendering, gate, claims).
// Every value that changes where a car is belongs to the version: clients that report different versions
// never agree on a tile's road hash (traffic/gate.ts), so mixed builds spawn nothing instead of different cars.
import type { QualityTier } from '../render/quality';

export const TRAFFIC = {
  /** Algorithm version, part of every agreed road hash. Bump it with any change to positions or identities. */
  version: 1,
  /** Shared clock origin: traffic time is seconds since this UTC instant (plus the server offset in multiplayer). */
  epochMs: Date.UTC(2026, 0, 1),
  /** Time headway between consecutive slots on every lane (s). One global value keeps lanes phase-locked. */
  headway: 6,
  /** Lane width (m): one v1 lane per direction sits half a width right of the centreline. */
  laneWidth: 3.2,
  /** Road classes (roadGraph.ts) that carry traffic: 2 street, 3 major. Service roads (1) are lots and driveways. */
  minClass: 2,
  /** Cruise speed by class without G3 speeds (m/s). */
  classSpeed: [0, 0, 30 / 3.6, 40 / 3.6] as readonly number[],
  /** Distance a lane stops short of a junction node (m); the connector curve crosses the junction. */
  junctionTrim: 5.5,
  /** Same at a dead end, where cars turn around. */
  deadEndTrim: 1.6,
  /** A node this close to the tile boundary is a seam crossing (roads.json clips at the bbox, 0.1 m precision). */
  seamTolerance: 0.15,
  /** Two tiles' seam crossings closer than this along the seam are the same street. */
  seamMatch: 0.6,
  /** Half-width of the box filter that turns timetable corners (stops, speed changes) into smooth motion (s). */
  smoothing: 1.4,
  /** A seam-to-seam lane may not slow below this share of its cruise speed to meet the next tile's phase. */
  minSpeedShare: 0.35,
} as const;

/** Moving-car caps per quality tier (street-richness plan §3.6/§7); the Air default is the low cap. */
export const TRAFFIC_CAPS: Readonly<Record<QualityTier, number>> = { mobile: 10, low: 15, medium: 30, high: 40 };

/** Population ring (plan §3.6, provisional until the GTA host measurements). */
export const RING = {
  /** In view, a car is created only this far out (m); it is never created closer inside the view cone. */
  createInViewMin: 90, createInViewMax: 200,
  /** Out of view, from this far (m) up to the out-of-view removal radius. */
  createOutOfViewMin: 40,
  /** In view a car stays until this far (m, about the 3×3 window edge). */
  removeInView: 300,
  /** Out of view a car is removed beyond this (m). */
  removeOutOfView: 85,
  /** Extra half-angle around the camera's horizontal half field of view that still counts as in view (rad). */
  viewMargin: 0.2,
  /** Cars within this distance of the player carry a kinematic Rapier body (m); bodies go at `bodyOff`. */
  bodyOn: 70, bodyOff: 85,
  /** Activation runs this often (s); active cars move every physics step. */
  activationPeriod: 0.2,
} as const;

/** Recolourable parked-car bodies (actors/parkedPaint.ts) and their deterministic shares. */
export const TRAFFIC_MODELS = ['sedan', 'suv', 'hatchback-sports', 'van'] as const;
export const MODEL_WEIGHTS = [0.4, 0.25, 0.22, 0.13] as const;
export const TRAFFIC_PAINTS = ['silver', 'white', 'charcoal', 'blue', 'red', 'beige'] as const;
export const PAINT_WEIGHTS = [0.22, 0.2, 0.22, 0.14, 0.11, 0.11] as const;
/** Width, height and length (m) of each body, as the generator scales them (pipeline/turku3d/parked/models.py). */
export const MODEL_SIZE: Readonly<Record<typeof TRAFFIC_MODELS[number], readonly [number, number, number]>> = {
  sedan: [1.85, 1.48, 4.55], 'hatchback-sports': [1.8, 1.46, 4.15], suv: [1.96, 1.78, 4.7], van: [1.98, 2.05, 5.0],
};
/** Chassis centre above the road, as parked cars (world/parkedVehicles.ts chassisLift). */
export const CHASSIS_LIFT = 0.75;

/** Claimed slots travel as registry sources `tr:<tile>:<origin lane>:<origin slot>` (net/sharedVehicles.ts). */
export const CLAIM_PREFIX = 'tr:';
