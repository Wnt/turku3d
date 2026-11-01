import type * as THREE from 'three/webgpu';
import type { DraftFreshness } from './coarseFreshness';
import type { CollisionBundle } from './collisionBundle';
import type { Manifest, ManifestEntry, ParkedVehicle } from './manifest';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import type { TileLightsField } from '../render/lights/tileLights';

export interface CoarseFragment { generation?: string; stale?: boolean; pass_kind?: 'first' | 'full'; coarse: true; tile: string; visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry>;
  /** The tile's OSM street graph (absent without an OSM extract). */
  collision_bundle?: CollisionBundle;
  roads?: { url: string; bytes: number } | null; vehicles?: ParkedVehicle[]; vehicle_models?: Manifest['vehicle_models'];
  /** The generator's street lights for this tile (render/lights/tileLights.ts). */
  lights?: TileLightsField | null }
/** `built`: the service has it but it is not loaded (a prefetched tile outside the 3x3 window). */
type State = 'pending' | 'loading' | 'built' | 'draft' | 'failed';
export interface Entry extends DraftFreshness {
  state: State; model?: THREE.Object3D; nextPoll: number; error?: string; roads?: RoadGraphJson;
  /** The collision entries this tile put into the manifest (dropped only while they are still there). */
  collision: Map<string, ManifestEntry>;
  vehicles?: ParkedVehicle[];
  /** The light list of the fragment whose model is drawn. */
  lights?: TileLightsField;
  pass?: 'first' | 'full'; comparison?: THREE.Object3D; comparing?: boolean; comparisonMode?: string; comparisonNext?: number;
  /** The service's estimate (clock ms) of when a queued or building tile is built. */
  readyAt?: number;
  /** The service's build counter at this entry's last poll. */
  version?: number; visualHash?: string; refreshing?: boolean;
  /** Visible requests own a model/collision pair; ahead requests own only service metadata. */
  resident?: boolean; retries?: number;
  preview?: CoarseFragment;
}

export interface CoarseHost {
  scene: THREE.Scene;
  renderer: THREE.WebGPURenderer;
  /** Optional detailed-mesh radius in metres; collision surfaces still prepare beyond it. */
  detailRadius?: number;
  /** Runtime residency inventory populated by draft fragments. */
  manifest: Manifest;
  unloadCollision: (id: string) => void;
  /** Loaded collision tiles whose manifest entry changed are fetched again and swapped (CollisionStreamer.refresh). */
  refreshCollision: (ids: string[]) => void;
  /** Decodes a draft .col in the physics worker (PhysicsClient.checkTile); rejects when the worker cannot use it. */
  checkCollision: (buffer: ArrayBuffer) => Promise<unknown>;
  /** Retain validated bytes for the distance streamer; no second HTTP fetch to build colliders. */
  retainCollision?: (id: string, entry: ManifestEntry, buffer: ArrayBuffer) => void;
  previewCollision?: (buffers: ReadonlyMap<string, ArrayBuffer>) => Promise<THREE.Object3D>;
  /** After material styling and draft tint: register derived runtime geometry here. */
  styled?: (model: THREE.Object3D) => void | Promise<void>;
  trackModel?: (model: THREE.Object3D, id: string) => void;
  untrackModel?: (model: THREE.Object3D, id: string) => void;
  style?: (model: THREE.Object3D) => void | Promise<void>;
  unstyle?: (model: THREE.Object3D) => void;
  /** Scene detachment is immediate; release render resources only after a submitted frame completes. */
  retire?: (release: () => void) => void;
  fetch?: typeof fetch;
  loadModel?: (url: string) => Promise<THREE.Object3D>;
  /** Parent of the always-visible "draft tile" badge (default document.body; none without a DOM). */
  badgeParent?: HTMLElement;
  /** Page URL that /coarse/ and the manifest resolve against (default location.href). */
  pageUrl?: string;
}
