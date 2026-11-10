import type { CityName } from './core/city';
import type { WeatherClock } from './render/weather/schedule';
import type * as THREE from 'three/webgpu';
import type { V3 } from './core/origin';
import type { CollisionStreamer } from './world/collisionStreaming';
import type { LoadingScreen } from './ui/loadingScreen';
import type { PhysicsClient } from './physics/client';
import type { TrafficClock } from './physics/protocol';
import type { Manifest } from './world/manifest';
import type { CoarseTiles } from './world/coarseTiles';
import type { VisualTiles } from './world/tiles';
import type { Telemetry } from './telemetry';
import type { Environment } from './render/environment';
import type { DayClock } from './render/dayClock';
import type { PerformanceMonitor } from './render/performance';
import type { MaterialStyler } from './render/materials';
import type { VegetationLod } from './world/vegetationLod';
import type { StreetLights } from './render/lights';
import type { parseParams } from './core/params';

type Pose = V3 & { yaw: number };
/** The world main.ts creates once per page and keeps across hot swaps of the session. */
export interface PlayWorld {
  params: ReturnType<typeof parseParams>;
  backend: string;
  renderer: THREE.WebGPURenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  env: Environment;
  perf: PerformanceMonitor;
  tiles: VisualTiles;
  /** The current vegetation renderer (replaceable on the dev server) and the street lights, for the object editor. */
  vegetation: () => VegetationLod | null;
  lights: StreetLights | null;
  materials: MaterialStyler | null;
  physics: PhysicsClient;
  rapier: string;
  manifest: Manifest;
  streamer: CollisionStreamer;
  coarse: CoarseTiles | null;
  telemetry: Telemetry | null;
  /** Rendered frames and simulated seconds (tests/e2e/waits.ts). */
  clock: { frames: number; simSeconds: number };
  errors: string[];
  fail: (e: unknown) => void;
  hudEl: HTMLElement;
  loadingScreen: LoadingScreen;
  renderFrame: () => void;
  prepareGraphics: (model: THREE.Object3D) => Promise<void>;
  /** The rendered clock: the shared cycle, or a local view (?time=, ?cycle=, the developer time panel). */
  dayClock: DayClock;
  weather: WeatherClock;
  /** The shared clock; restoration only affects an unpinned developer clock. */
  clockTime: () => Date;
  /** The clock single-player traffic density follows (DayClock.trafficClock). */
  trafficClock: () => TrafficClock;
  syncClock?: (serverTime: number) => void;
  /** The pause menu's graphics presets (main.ts switchQuality): `set` switches what a running session can and returns
   *  what still waits for a reload; `pending` is that list for the preset in use. */
  graphics: { set(tier: import('./render/quality').QualityTier): string[]; pending(): string[]; boot: import('./render/quality').QualityTier };
  /** Shared UTC milliseconds (synchronised with the multiplayer server); deterministic world simulations use it. */
  sharedNow: () => number;
  setClock: (date: Date) => void;
  /** Ground height under (x, z) from a physics ray. */
  groundAt: (x: number, z: number) => Promise<number>;
  /** The page's start, and the start car's parking (both placed in physics by main.ts): the city's start, or the
   *  `?spawn=` pose (a portal exit, a tool's URL). */
  spawn: Pose;
  car: Pose;
  /** The requested point the page's start was searched from (the same on every client). */
  startAt: { x: number; z: number };
  /** On a `?spawn=` page: finds the city's own start (SaveGameController `cityStart`). */
  cityStart?: (stream: (x: number, z: number) => Promise<void>) => Promise<{ spawn: Pose; car: Pose; at: { x: number; z: number } }>;
  /** Switches the page to `to` in place (main.ts): the session ends and a new one starts there. `spawn` is the start
   *  in that city's grid (a portal exit); absent, its centre spawn. Absent in modes without travel. */
  travel?: (to: CityName, spawn?: { E: number; N: number; headingDeg: number }) => Promise<void>;
}

