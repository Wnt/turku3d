import type * as THREE from 'three/webgpu';
import type { V3 } from './core/origin';
import type { CollisionStreamer } from './world/collisionStreaming';
import type { LoadingScreen } from './ui/loadingScreen';
import type { PhysicsClient } from './physics/client';
import type { Manifest } from './world/manifest';
import type { CoarseTiles } from './world/coarseTiles';
import type { VisualTiles } from './world/tiles';
import type { Telemetry } from './telemetry';
import type { Environment } from './render/environment';
import type { PerformanceMonitor } from './render/performance';
import type { MaterialStyler } from './render/materials';
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
  /** The shared clock; restoration only affects an unpinned developer clock. */
  clockTime: () => Date;
  syncClock?: (serverTime: number) => void;
  /** Shared UTC milliseconds (synchronised with the multiplayer server); deterministic world simulations use it. */
  sharedNow: () => number;
  setClock: (date: Date) => void;
  /** Ground height under (x, z) from a physics ray. */
  groundAt: (x: number, z: number) => Promise<number>;
  /** Where a new game starts, and the start car's parking (both placed in physics by main.ts). */
  spawn: Pose;
  car: Pose;
}

