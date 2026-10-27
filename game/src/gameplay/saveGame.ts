// The running game's side of the save slots (core/save.ts) and menus (ui/menu.ts): capture the game into a slot,
// place a loaded or new game in the world, the autosave triggers and the pause menu's frame handling.
import * as THREE from 'three/webgpu';
import { collisionTileAt, enToEngine, engineToEN, headingToYaw, type V3 } from '../core/origin';
import { Autosave, formatLabel, type SaveGame, type SaveStore } from '../core/save';
import type { Input } from '../input';
import type { PhysicsClient } from '../physics/client';
import type { TimeControl } from '../render/debug';
import type { PauseMenu } from '../ui/menu';
import { SaveMenuFlow, type GameHooks } from '../ui/menu';
import type { StartScreen } from '../ui/startScreen';
import type { CollisionStreamer } from '../world/collisionStreaming';
import type { Manifest } from '../world/manifest';
import { OWN_VEHICLE_ID, SCOOTER_ID, ownedVehicleId, type MissionSystem } from './missions/missions';
import type { VehicleKind } from '../physics/constants';
import type { SafeReset } from './safeReset';

type Quat = { x: number; y: number; z: number; w: number };
const quat = new THREE.Quaternion(), axis = new THREE.Vector3();
const down = { x: 0, y: -1, z: 0 };

/** Chassis yaw (engine radians) of a physics vehicle. */
export function yawOf(rot: Quat) {
  axis.set(1, 0, 0).applyQuaternion(quat.set(rot.x, rot.y, rot.z, rot.w));
  return Math.atan2(-axis.z, axis.x);
}

/** Where the player stands after leaving a vehicle: the driver side (-Z in the chassis frame), beside a scooter. */
export function driverSide(car: { pos: V3; rot: Quat; kind?: VehicleKind }): V3 {
  axis.set(0, 0, car.kind === 'scooter' ? -0.9 : -(1.0 + 0.9)).applyQuaternion(quat.set(car.rot.x, car.rot.y, car.rot.z, car.rot.w));
  return { x: car.pos.x + axis.x, y: car.pos.y + 0.2, z: car.pos.z + axis.z };
}

type Pose = V3 & { yaw: number };

/** The kerb 5.5 m behind a parked car (`yaw` turns the chassis +X axis): where a second vehicle waits beside it. */
export function kerbBehind(car: Pose): Pose {
  return { x: car.x - 5.5 * Math.cos(car.yaw), y: car.y, z: car.z + 5.5 * Math.sin(car.yaw), yaw: car.yaw };
}

interface SaveGameOptions {
  physics: PhysicsClient; streamer: CollisionStreamer; manifest: Manifest; missions: MissionSystem; recovery: SafeReset;
  saves: SaveStore; startScreen: StartScreen; pauseMenu: PauseMenu; clockControl: TimeControl;
  /** The ?time= start, used by a new game. */
  defaultTime: Date;
  clock: () => Date;
  setClock: (date: Date) => void;
  /** Where a new game starts, and the own car's spawn parking. */
  spawn: Pose; car: Pose;
  /** The live player: mode, driven vehicle, feet and camera yaw. */
  view: () => { mode: 'foot' | 'car'; drivenId: string | null; feet: V3; cameraYaw: number };
  /** Leaves the driven car without placing the character. */
  leaveCar: () => void;
  /** The character now stands at `at`; the camera turns to `yaw`. */
  placed: (at: V3, yaw: number) => void;
  setPaused: (paused: boolean) => void;
  /** Opening story shown after a new solo game is placed. */
  showIntro: () => Promise<void>;
  fail: (e: unknown) => void;
  multiplayer?: () => void;
  scoreboardStart?: () => void;
  scoreboardPause?: () => void;
  /** Opens the pause menu's Developer page (localhost only, ui/devMenu.ts). */
  developer?: () => void;
  onQuit?: () => void;
}

/** A teleport where no ground is found (outside the built area) arrives at this height (N2000 m), flying. */
const TELEPORT_AIR_HEIGHT = 60;

/** Only the explicit New game action passes null; Continue and Load pass a slot. */
export function queuesIntro(game: SaveGame | null): boolean { return game === null; }

export class SaveGameController {
  /** A loaded or new game is being placed: the world waits (paused) until the player stands on the ground. */
  loading = false;
  readonly autosave: Autosave;
  readonly hooks: GameHooks;
  readonly startFlow: SaveMenuFlow;
  private readonly pauseFlow: SaveMenuFlow;
  /** Collision streams around this point instead of the feet until the placed character reports it. */
  private focus: { x: number; z: number } | null = null;
  private readonly detach: () => void;

  constructor(private readonly o: SaveGameOptions) {
    this.hooks = {
      capture: () => this.capture(),
      play: game => this.play(game),
      resume: () => { o.pauseMenu.close(); o.setPaused(false); },
      respawn: () => { void this.respawn().catch(o.fail); },
      quit: () => {
        o.onQuit?.();
        this.autosave.save('quit');
        o.pauseMenu.close();
        o.setPaused(false);
        o.startScreen.reopen(this.startFlow.start());
      },
    };
    this.startFlow = new SaveMenuFlow(o.startScreen, o.saves, this.hooks, o.multiplayer, o.scoreboardStart);
    this.pauseFlow = new SaveMenuFlow(o.pauseMenu, o.saves, this.hooks, undefined, o.scoreboardPause, o.developer);
    this.autosave = new Autosave(o.saves, () => o.startScreen.active || this.loading ? null : this.capture());
    this.detach = this.autosave.attach(window, document);
  }

  /** Stops the page-hide autosave (a hot-swapped play session). */
  dispose() { this.detach(); }
  pausePage() { return this.pauseFlow.pause(); }

  /** The running game as a save slot. */
  capture(): SaveGame {
    const { physics, missions } = this.o;
    const view = this.o.view();
    const driven = view.mode === 'car' ? physics.latest?.vehicles.find(v => v.id === view.drivenId) : undefined;
    const at = driven ? driverSide(driven) : view.feet;
    const p = engineToEN(at.x, at.y, at.z);
    // The owned vehicle (the scooter or the start car), else the start car where it was left.
    const ownId = ownedVehicleId(missions.vehicle) ?? OWN_VEHICLE_ID;
    const own = physics.latest?.vehicles.find(v => v.id === ownId);
    const c = own ? engineToEN(own.pos.x, own.pos.y, own.pos.z) : null;
    const clock = this.o.clock();
    return { version: 1, savedAt: Date.now(), label: formatLabel(missions.lastAddress, clock), mission: missions.serialize(),
      // In a car the camera sits behind the chassis: its yaw is the chassis yaw minus a quarter turn.
      player: { E: p.E, N: p.N, H: p.H, yaw: driven ? yawOf(driven.rot) - Math.PI / 2 : view.cameraYaw },
      vehicle: own && c ? { owned: missions.vehicle !== 'none', E: c.E, N: c.N, H: c.H, yaw: yawOf(own.rot) } : null,
      clock: { timeOfDay: clock.toISOString() }, stats: { deliveries: missions.deliveries, money: missions.money, devMoney: missions.devMoney } };
  }

  /**
   * Per frame after the shop menu: the pause action opens the pause menu; while it is open it owns the menu input
   * (Menu resumes from any page, Escape and B go back). Returns whether the pause menu was open this frame.
   */
  frame(input: Input): boolean {
    const menu = this.o.pauseMenu;
    if (menu.open) {
      const pause = input.consume('pause');
      const back = menu.panel.handle(input);
      if (pause && !back && menu.open) this.hooks.resume();
      return true;
    }
    if (input.consume('pause') && !this.loading) {
      this.o.setPaused(true);
      menu.show(this.pauseFlow.pause());
    }
    return false;
  }

  /** The point collision streams around this frame. */
  streamPoint(feet: V3): { x: number; z: number } {
    if (this.focus && !this.loading && Math.hypot(feet.x - this.focus.x, feet.z - this.focus.z) < 3) this.focus = null;
    return this.focus ?? feet;
  }

  private play(game: SaveGame | null) {
    if (this.o.startScreen.active) this.o.startScreen.close();
    this.o.pauseMenu.close();
    this.loading = true;
    this.o.setPaused(true);
    void this.place(game).then(() => queuesIntro(game) ? this.o.showIntro() : undefined)
      .catch(this.o.fail).finally(() => { this.loading = false; this.o.setPaused(false); });
  }

  /** Places a multiplayer join at the server's spawn or retained pose, using the normal safe-ground snap. */
  async playAt(at: Pose) {
    this.o.startScreen.close();
    this.o.pauseMenu.close();
    this.loading = true;
    this.o.setPaused(true);
    try { await this.place(null, at); }
    finally { this.loading = false; this.o.setPaused(false); }
  }

  /** Puts the world in the state of `game` (null: a new game): missions, clock, the own car, then the player. */
  private async place(game: SaveGame | null, override?: Pose) {
    const { physics, missions, spawn } = this.o;
    if (this.o.view().mode === 'car') this.o.leaveCar();
    missions.restore(game?.mission ?? null);
    this.o.setClock(game?.clock ? new Date(game.clock.timeOfDay) : this.o.defaultTime);
    this.o.clockControl.set(this.o.clock());
    const saved = game?.vehicle?.owned ? { ...enToEngine(game.vehicle.E, game.vehicle.N, game.vehicle.H), yaw: game.vehicle.yaw } : null;
    const scooter = missions.vehicle === 'scooter';
    const car = saved && !scooter ? saved : this.o.car;
    const carY = saved && !scooter ? await this.snap(car) : (await this.settle(car.x, car.z), car.y);
    physics.resetVehicle({ x: car.x, y: carY + 0.75, z: car.z }, car.yaw, OWN_VEHICLE_ID);
    if (scooter) {
      // The saved scooter, or (a save from before the scooter) at the kerb behind the start car.
      const at = saved ?? kerbBehind(car);
      const y = await this.snap(at);
      physics.spawnVehicle({ x: at.x, y: y + 0.55, z: at.z }, at.yaw, { id: SCOOTER_ID, kind: 'scooter' });
    }
    const at = override ?? (game?.player ? { ...enToEngine(game.player.E, game.player.N, game.player.H), yaw: game.player.yaw } : spawn);
    const y = override || game?.player ? await this.snap(at) : (await this.settle(at.x, at.z), at.y);
    await this.putCharacter({ x: at.x, y, z: at.z }, at.yaw);
    missions.begin();
  }

  /**
   * Developer teleport (localhost only) with `?spawn=E,N,heading` semantics: the ground under E/N seen from the sky,
   * placed like a loaded game (the world waits paused, safe reset restarts there). Without ground (outside the
   * built area) the player arrives at TELEPORT_AIR_HEIGHT; resolves whether ground was found.
   */
  teleport(E: number, N: number, headingDeg: number): Promise<boolean> {
    return this.relocating(async () => {
      if (this.o.view().mode === 'car') this.o.leaveCar();
      const at = enToEngine(E, N, 0);
      await this.settle(at.x, at.z);
      const ground = (await this.o.physics.raycast({ x: at.x, y: 500, z: at.z }, down, 1000))?.point.y ?? null;
      await this.putCharacter({ x: at.x, y: ground ?? TELEPORT_AIR_HEIGHT, z: at.z }, headingToYaw(headingDeg));
      return ground !== null;
    });
  }

  /**
   * Respawn at the start (the pause menu, every build): the player goes back to where a new game starts, with the
   * ground snap and safe-reset restart of a loaded game; money, missions and a running order are untouched. Vehicles
   * stay where they are, except the driven one: the own car returns to its start parking, any other to the kerb
   * behind it.
   */
  respawn(): Promise<void> {
    return this.relocating(async () => {
      const { physics, spawn, car } = this.o;
      const view = this.o.view();
      if (view.mode === 'car') {
        this.o.leaveCar();
        if (view.drivenId) {
          const spot = view.drivenId === OWN_VEHICLE_ID ? car : kerbBehind(car);
          const y = await this.snap(spot);
          physics.resetVehicle({ x: spot.x, y: y + 0.75, z: spot.z }, spot.yaw, view.drivenId);
        }
      }
      await this.putCharacter({ x: spawn.x, y: await this.snap(spawn), z: spawn.z }, spawn.yaw);
    });
  }

  /** Closes the pause menu and keeps the world paused (`loading`) while `work` moves the player. */
  private async relocating<T>(work: () => Promise<T>): Promise<T> {
    this.o.pauseMenu.close();
    this.loading = true;
    this.o.setPaused(true);
    try {
      return await work();
    } finally {
      this.loading = false;
      this.o.setPaused(false);
    }
  }

  /** Puts the character's feet at `feet` and waits (paused) until the worker reports it there; safe reset restarts. */
  private async putCharacter(feet: V3, yaw: number) {
    const { physics } = this.o;
    physics.setCharacterEnabled(true, { x: feet.x, y: feet.y + 0.05, z: feet.z });
    // The world stays paused until the worker reports the character at its new place, so nothing reads the old one.
    const t0 = performance.now();
    const arrived = () => { const c = physics.latest?.character?.pos; return !!c && Math.hypot(c.x - feet.x, c.z - feet.z) < 0.5; };
    while (!arrived()) {
      if (performance.now() - t0 > 10000) throw new Error('the placed character did not arrive');
      physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
      await new Promise(r => setTimeout(r, 20));
    }
    this.o.placed(feet, yaw);
    this.o.recovery.restart();
  }

  /** Streams the collision tile under (x, z) and waits until queries see it. */
  private async settle(x: number, z: number) {
    const { physics, streamer, manifest } = this.o;
    this.focus = { x, z };
    streamer.update(x, z, true);
    const t = collisionTileAt(x, z), id = `c_${t.i}_${t.j}`;
    const t0 = performance.now();
    while (manifest.collision[id] && !physics.loaded.has(id)) {
      if (performance.now() - t0 > 30000) throw new Error(`collision tile ${id} did not load`);
      await new Promise(r => setTimeout(r, 20));
      streamer.update(x, z);
    }
    physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
  }

  /**
   * Ground under a saved pose, the same rule as safe reset (gameplay/safeReset.ts): a downward ray from 2 m above
   * the saved height (under a bridge it finds the road, not the deck), else from the sky, else the saved height.
   */
  private async snap(p: V3): Promise<number> {
    await this.settle(p.x, p.z);
    const { physics } = this.o;
    const hit = await physics.raycast({ x: p.x, y: p.y + 2, z: p.z }, down, 60)
      ?? await physics.raycast({ x: p.x, y: 500, z: p.z }, down, 1000);
    return hit?.point.y ?? p.y;
  }
}
