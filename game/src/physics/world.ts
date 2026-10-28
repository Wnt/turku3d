// Rapier world used by the physics worker (and directly by the Node unit tests).
// Everything is in engine world-local coordinates; each .col tile's float64 origin becomes its collider translation.
import type * as RAPIER_NS from '@dimforge/rapier3d';
import { collisionTileId, type V3 } from '../core/origin';
import { collisionCovers } from './collisionCoverage';
import { GROUP, materialClass, MATERIALS, MAT } from '../shared/col/materials';
import { readCol, COL_FLAG_FIX_INTERNAL_EDGES, type ColTile } from './colTile';
import { TriLocator } from './triLocator';
import { EdgeWalls } from './edgeWalls';
import { CHARACTER, DT, MAX_SUBSTEPS, VEHICLES } from './constants';
import { Vehicle } from '../vehicle/vehicle';
import { CurbQueries } from '../vehicle/curbQueries';
import { remoteColliderHeight } from '../net/height';
import { addCurbMesh, addCurbPrimitive } from '../vehicle/tileCurbs.ts';
export { CHARACTER, DT };
import type { CharacterInput, CharacterState, PoleSpec, PoleState, RayFilter, RayHit, RemoteBody, TileStats, VehicleInput, VehicleState, VehicleSpec } from './protocol';

type R = typeof RAPIER_NS;

/** Rapier interaction groups: high 16 bits membership, low 16 bits filter. */
const groups = (membership: number, filter: number) => ((membership & 0xffff) << 16) | (filter & 0xffff);

interface ColliderInfo { tile: string; triMat: Uint8Array | null; mat: number; locator: TriLocator | null }
interface LoadedTile { stats: TileStats; colliders: RAPIER_NS.Collider[]; cell: [number, number] }

export class PhysicsWorld {
  readonly world: RAPIER_NS.World;
  private tiles = new Map<string, LoadedTile>();
  private residentCells = new Set<string>();
  private heldVehicles = new Set<string>();
  private vehicleRadii = new Map<string, number>();
  private colliderInfo = new Map<number, ColliderInfo>();
  private acc = 0;
  tick = 0;
  previousCharacter: CharacterState | null = null;
  previousVehicle: VehicleState | null = null;
  private jumpPending = false;
  get remainder() { return this.acc; }

  // character
  private cc: RAPIER_NS.KinematicCharacterController | null = null;
  private charCollider: RAPIER_NS.Collider | null = null;
  private charVy = 0;
  private charGrounded = false;
  private charVel = { x: 0, y: 0, z: 0 };
  private charEnabled = true;

  // vehicle
  private vehicles = new Map<string, Vehicle>();
  private vehicleSnapshots = new Map<string, { state: VehicleState; sleeping: boolean }>();
  private poleSnapshots = new Map<string, { state: PoleState; resting: boolean }>();
  private drivenId: string | null = 'player';
  private nearbyVehicles = new Set<string>();
  private remotes = new Map<string, { body: RAPIER_NS.RigidBody; mode: RemoteBody['mode'] }>();
  private poles = new Map<string, { spec: PoleSpec; body: RAPIER_NS.RigidBody; anchor: RAPIER_NS.RigidBody; joint: RAPIER_NS.ImpulseJoint | null; brokenAt: number | null; brokeNow: boolean }>();
  private brokenPoles = new Set<string>();
  private poleEvents: RAPIER_NS.EventQueue;
  get vehicle(): Vehicle | null { return this.vehicles.get('player') ?? null; }
  private curbQueries: CurbQueries;
  /** Walls along the true edges of the collision (edgeWalls.ts). */
  readonly edges: EdgeWalls;

  constructor(private readonly R: R) {
    this.world = new R.World({ x: 0, y: -9.81, z: 0 });
    this.world.timestep = DT;
    this.poleEvents = new R.EventQueue(true);
    this.curbQueries = new CurbQueries(R, this.world);
    this.edges = new EdgeWalls(R, this.world);
  }

  // ------------------------------------------------------------------ tiles
  async loadTile(id: string, buffer: ArrayBuffer): Promise<TileStats> {
    if (this.tiles.has(id)) this.unloadTile(id);
    const t0 = performance.now();
    const col = await readCol(buffer);
    const colliders = this.buildTile(id, col);
    const stats: TileStats = { id, tris: col.indices.length / 3, prims: col.prims.length, bytes: buffer.byteLength, buildMs: +(performance.now() - t0).toFixed(2), colliders: colliders.length, curbTris: colliders.reduce((n, c) => n + this.curbQueries.triangleCount(c.handle), 0) };
    this.tiles.set(id, { stats, colliders, cell: [col.tileI, col.tileJ] });
    this.residentCells.add(collisionTileId(col.tileI, col.tileJ));
    const [lo, hi] = heightRange(col);
    this.edges.add(col.tileI, col.tileJ, lo, hi);
    return stats;
  }

  /** Decodes a tile without adding it (draft tiles are drawn only after their collision passes this). */
  async checkTile(buffer: ArrayBuffer): Promise<{ tris: number; prims: number }> {
    const col = await readCol(buffer);
    if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
    return { tris: col.indices.length / 3, prims: col.prims.length };
  }

  private buildTile(id: string, col: ColTile): RAPIER_NS.Collider[] {
    const R = this.R;
    const out: RAPIER_NS.Collider[] = [];
    const [ox, oy, oz] = col.origin;
    const triCount = col.materials.length;
    // split triangles by collision class -> one trimesh per class present
    const byClass: Record<'solid' | 'query' | 'sensor', number[]> = { solid: [], query: [], sensor: [] };
    for (let t = 0; t < triCount; t++) byClass[materialClass(col.materials[t])].push(t);
    const fixEdges = (col.flags & COL_FLAG_FIX_INTERNAL_EDGES) !== 0;
    for (const cls of ['solid', 'query', 'sensor'] as const) {
      const tris = byClass[cls];
      if (!tris.length) continue;
      const idx = new Uint32Array(tris.length * 3);
      const triMat = new Uint8Array(tris.length);
      tris.forEach((t, k) => { idx[3 * k] = col.indices[3 * t]; idx[3 * k + 1] = col.indices[3 * t + 1]; idx[3 * k + 2] = col.indices[3 * t + 2]; triMat[k] = col.materials[t]; });
      const flags = cls === 'solid' && fixEdges ? R.TriMeshFlags.FIX_INTERNAL_EDGES : undefined;
      const desc = R.ColliderDesc.trimesh(col.positions, idx, flags).setTranslation(ox, oy, oz);
      this.applyClass(desc, cls, 0.85);
      const c = this.world.createCollider(desc);
      this.colliderInfo.set(c.handle, { tile: id, triMat, mat: triMat[0], locator: new TriLocator(col.positions, idx, col.origin) });
      const helper = addCurbMesh(R, this.curbQueries, c, col.positions, idx, triMat, col.origin);
      if (helper) out.push(helper);
      out.push(c);
    }
    for (const p of col.prims) {
      let desc: RAPIER_NS.ColliderDesc | null = null;
      if (p.kind === 1) desc = R.ColliderDesc.cuboid(p.params[0], p.params[1], p.params[2]);
      else if (p.kind === 2) desc = R.ColliderDesc.capsule(p.params[0], p.params[1]);
      else if (p.kind === 3) desc = R.ColliderDesc.cylinder(p.params[0], p.params[1]);
      if (!desc) { console.warn(`[physics] ${id}: unknown primitive kind ${p.kind}, skipped`); continue; }
      desc.setTranslation(ox + p.center[0], oy + p.center[1], oz + p.center[2])
        .setRotation({ x: p.rotation[0], y: p.rotation[1], z: p.rotation[2], w: p.rotation[3] });
      this.applyClass(desc, materialClass(p.material), MATERIALS[p.material]?.friction ?? 0.8);
      const c = this.world.createCollider(desc);
      this.colliderInfo.set(c.handle, { tile: id, triMat: null, mat: p.material, locator: null });
      const helper = addCurbPrimitive(R, this.curbQueries, c, p.material);
      if (helper) out.push(helper);
      out.push(c);
    }
    return out;
  }

  private applyClass(desc: RAPIER_NS.ColliderDesc, cls: 'solid' | 'query' | 'sensor', friction: number) {
    if (cls === 'solid') desc.setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00)).setFriction(friction);
    else if (cls === 'query') desc.setCollisionGroups(groups(GROUP.STATIC_QUERY, 0xff00)).setSolverGroups(0);
    // 0xff00: scene queries; only the 'water' ray filter includes sensors, every other query excludes them.
    else desc.setSensor(true).setCollisionGroups(groups(GROUP.STATIC_SENSOR, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00));
  }

  unloadTile(id: string): boolean {
    const t = this.tiles.get(id);
    if (!t) return false;
    for (const c of t.colliders) { this.colliderInfo.delete(c.handle); this.curbQueries.remove(c.handle); this.world.removeCollider(c, false); }
    this.tiles.delete(id);
    this.residentCells.delete(collisionTileId(...t.cell));
    this.edges.remove(...t.cell);
    return true;
  }

  tileStats(): TileStats[] { return [...this.tiles.values()].map((t) => t.stats); }
  hasTile(id: string): boolean { return this.tiles.has(id); }

  // ------------------------------------------------------------------ character
  spawnCharacter(feet: V3) {
    const R = this.R;
    if (!this.cc) {
      const cc = this.world.createCharacterController(CHARACTER.skin);
      cc.setUp({ x: 0, y: 1, z: 0 });
      cc.enableAutostep(0.2, CHARACTER.autostepMinWidth, false);
      cc.enableSnapToGround(CHARACTER.snap);
      cc.setMaxSlopeClimbAngle((CHARACTER.maxClimbDeg * Math.PI) / 180);
      cc.setMinSlopeSlideAngle((CHARACTER.minSlideDeg * Math.PI) / 180);
      cc.setSlideEnabled(true);
      cc.setApplyImpulsesToDynamicBodies(true);
      this.cc = cc;
      this.charCollider = this.world.createCollider(
        R.ColliderDesc.capsule(CHARACTER.halfHeight, CHARACTER.radius)
          .setCollisionGroups(groups(GROUP.CHARACTER, GROUP.STATIC_SOLID | GROUP.VEHICLE | GROUP.DYNAMIC)),
      );
    }
    this.setCharacterFeet(feet);
    this.charVy = 0;
    this.charGrounded = false;
    this.charEnabled = true;
    this.charCollider!.setEnabled(true);
  }

  private setCharacterFeet(feet: V3) {
    this.charCollider!.setTranslation({ x: feet.x, y: feet.y + CHARACTER.halfHeight + CHARACTER.radius + CHARACTER.skin, z: feet.z });
  }

  setCharacterEnabled(enabled: boolean, feet?: V3) {
    if (!this.charCollider) return;
    if (feet) this.setCharacterFeet(feet);
    this.charEnabled = enabled;
    this.charCollider.setEnabled(enabled);
    this.charVy = 0;
  }

  private stepCharacter(input: CharacterInput) {
    const cc = this.cc!, col = this.charCollider!;
    const filter = groups(GROUP.CHARACTER, GROUP.STATIC_SOLID | GROUP.VEHICLE | GROUP.DYNAMIC);
    const flags = this.R.QueryFilterFlags.EXCLUDE_SENSORS;
    const p0 = col.translation();
    if (input.fly) {
      // Flight: the commanded velocity, collisions kept; leaving it falls from rest.
      cc.computeColliderMovement(col, { x: input.vx * DT, y: input.fly.vy * DT, z: input.vz * DT }, flags, filter);
      const m = cc.computedMovement();
      col.setTranslation({ x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z });
      this.charVy = 0;
      this.charGrounded = cc.computedGrounded();
      this.charVel = { x: m.x / DT, y: m.y / DT, z: m.z / DT };
      return;
    }
    // Rapier's native autostep depends on horizontal speed. A second, height-limited
    // clearance move below handles slow walks into steps up to 0.35 m.
    if (this.charGrounded && input.jump) this.charVy = CHARACTER.jumpSpeed;
    else if (this.charGrounded && this.charVy <= 0) this.charVy = CHARACTER.groundedVy;
    else this.charVy = Math.max(this.charVy - CHARACTER.gravity * DT, -50);
    const wanted = this.charVy * DT;
    cc.computeColliderMovement(col, { x: input.vx * DT, y: wanted, z: input.vz * DT }, flags, filter);
    let m = cc.computedMovement();
    let grounded = cc.computedGrounded();
    if (this.charGrounded && !input.jump && Math.hypot(input.vx, input.vz) > 0.01) {
      const requested = Math.hypot(input.vx, input.vz) * DT;
      const progressed = (m.x * input.vx + m.z * input.vz) / Math.hypot(input.vx, input.vz);
      if (progressed < requested * 0.5) {
        const speed = Math.hypot(input.vx, input.vz);
        const ahead = (CHARACTER.radius + requested) / speed;
        const feetY = p0.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin;
        const rear = this.raycast(
          { x: p0.x - input.vx * CHARACTER.radius / speed, y: feetY + 0.2, z: p0.z - input.vz * CHARACTER.radius / speed },
          { x: 0, y: -1, z: 0 }, 0.7,
        );
        const baseY = Math.min(feetY, rear?.point.y ?? feetY);
        const support = this.raycast(
          { x: p0.x + input.vx * ahead, y: feetY + CHARACTER.autostepHeight + 0.1, z: p0.z + input.vz * ahead },
          { x: 0, y: -1, z: 0 }, CHARACTER.autostepHeight + 0.4,
        );
        if (support && support.point.y - baseY > CHARACTER.autostepHeight + 0.002) {
          // The surface ahead is above the climb limit, even if capsule curvature
          // would let repeated small moves inch up its vertical face.
          col.setTranslation({ x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z });
          this.charGrounded = cc.computedGrounded();
          this.charVel = { x: m.x / DT, y: m.y / DT, z: m.z / DT };
          return;
        }
        const raised = CHARACTER.autostepHeight + CHARACTER.skin;
        cc.computeColliderMovement(col, { x: 0, y: raised, z: 0 }, flags, filter);
        const up = cc.computedMovement();
        if (up.y >= raised - CHARACTER.skin) {
          col.setTranslation({ x: p0.x, y: p0.y + up.y, z: p0.z });
          cc.computeColliderMovement(col, { x: input.vx * DT, y: 0, z: input.vz * DT }, flags, filter);
          const across = cc.computedMovement();
          const advance = (across.x * input.vx + across.z * input.vz) / Math.hypot(input.vx, input.vz);
          if (advance > progressed + 0.001) {
            col.setTranslation({ x: p0.x + across.x, y: p0.y + up.y + across.y, z: p0.z + across.z });
            cc.computeColliderMovement(col, { x: 0, y: -(raised + CHARACTER.snap), z: 0 }, flags, filter);
            const down = cc.computedMovement();
            const rise = up.y + across.y + down.y;
            if (cc.computedGrounded() && rise > -CHARACTER.skin && rise <= CHARACTER.autostepHeight + 0.002) {
              m = { x: across.x, y: rise, z: across.z };
              grounded = true;
            }
          }
          col.setTranslation(p0);
        }
      }
    }
    const p = { x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z };
    col.setTranslation(p);
    this.charGrounded = grounded;
    if (this.charVy > 0 && m.y < wanted * 0.5) this.charVy = 0; // bumped head
    this.charVel = { x: (p.x - p0.x) / DT, y: (p.y - p0.y) / DT, z: (p.z - p0.z) / DT };
  }

  characterState(): CharacterState | null {
    if (!this.charCollider) return null;
    const t = this.charCollider.translation();
    const feet = { x: t.x, y: t.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin, z: t.z };
    let groundMaterial = 0;
    if (this.charEnabled) {
      const hit = this.raycast({ x: t.x, y: t.y, z: t.z }, { x: 0, y: -1, z: 0 }, CHARACTER.halfHeight + CHARACTER.radius + 0.4, 'solid');
      groundMaterial = hit?.material ?? 0;
    }
    return { pos: feet, vel: { ...this.charVel }, grounded: this.charGrounded, enabled: this.charEnabled, groundMaterial };
  }

  // ------------------------------------------------------------------ vehicle
  spawnVehicle(pos: V3, yaw: number, spec: Partial<VehicleSpec> = {}) {
    const id = spec.id ?? 'player';
    const existing = this.vehicles.get(id);
    if (existing) { existing.reset(pos, yaw); return; }
    this.vehicles.set(id, new Vehicle(this.R, this.world, pos, yaw, this.curbQueries, spec));
    const h = spec.halfExt ?? VEHICLES[spec.kind ?? 'car'].halfExt;
    this.vehicleRadii.set(id, Math.hypot(h.x, h.y, h.z) + 0.5);
  }

  removeVehicle(id: string) {
    if (id === this.drivenId) return;
    const vehicle = this.vehicles.get(id);
    if (!vehicle) return;
    this.world.removeVehicleController(vehicle.controller);
    this.world.removeRigidBody(vehicle.body);
    this.vehicles.delete(id);
    this.vehicleRadii.delete(id);
    this.vehicleSnapshots.delete(id);
    this.heldVehicles.delete(id);
  }

  driveVehicle(id: string | null) {
    if (id && !this.vehicles.has(id)) throw new Error(`unknown vehicle ${id}`);
    this.drivenId = id;
    if (id) this.vehicles.get(id)!.body.wakeUp();
  }
  resetVehicle(pos: V3, yaw: number, id = 'player') { this.vehicles.get(id)?.reset(pos, yaw); }
  vehicleState(): VehicleState | null { return this.vehicles.get(this.drivenId ?? 'player')?.state() ?? null; }
  vehicleStates(): VehicleState[] {
    return [...this.vehicles].map(([id, vehicle]) => {
      const sleeping = vehicle.body.isSleeping(), old = this.vehicleSnapshots.get(id);
      if (old && sleeping && old.sleeping) return old.state;
      const state = { id, ...vehicle.state() };
      this.vehicleSnapshots.set(id, { state, sleeping });
      return state;
    });
  }

  /** Kinematic proxies are driven by interpolated network poses. They collide with the local KCC and vehicles. */
  setRemotes(bodies: RemoteBody[]) {
    const incoming = new Set(bodies.map(b => b.id));
    for (const [id, remote] of this.remotes) if (!incoming.has(id)) {
      this.world.removeRigidBody(remote.body);
      this.remotes.delete(id);
    }
    for (const p of bodies) {
      let remote = this.remotes.get(p.id);
      if (remote?.mode !== p.mode) {
        if (remote) this.world.removeRigidBody(remote.body);
        const height = remoteColliderHeight(p.mode);
        const body = this.world.createRigidBody(this.R.RigidBodyDesc.kinematicPositionBased()
          .setTranslation(p.pos.x, p.pos.y + height, p.pos.z));
        const half = p.mode === 'foot' ? null : VEHICLES[p.mode].halfExt;
        const desc = half ? this.R.ColliderDesc.cuboid(half.x, half.y, half.z)
          : this.R.ColliderDesc.capsule(CHARACTER.halfHeight, CHARACTER.radius);
        desc.setCollisionGroups(groups(GROUP.DYNAMIC, GROUP.CHARACTER | GROUP.VEHICLE));
        this.world.createCollider(desc, body);
        remote = { body, mode: p.mode };
        this.remotes.set(p.id, remote);
      }
      const y = p.pos.y + remoteColliderHeight(p.mode);
      remote!.body.setNextKinematicTranslation({ x: p.pos.x, y, z: p.pos.z });
      const yaw = p.mode === 'foot' ? p.yaw : p.yaw + Math.PI / 2;
      remote!.body.setNextKinematicRotation(p.mode !== 'foot' && p.rot ? p.rot : { x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) });
    }
  }

  setPoles(add: PoleSpec[], remove: string[]) {
    for (const id of remove) {
      this.poleSnapshots.delete(id);
      const pole = this.poles.get(id);
      if (!pole) continue;
      this.world.removeRigidBody(pole.body);
      this.world.removeRigidBody(pole.anchor);
      this.poles.delete(id);
    }
    for (const spec of add) {
      if (this.poles.has(spec.id)) continue;
      const centre = { x: spec.base.x, y: spec.base.y + spec.height / 2, z: spec.base.z };
      const anchor = this.world.createRigidBody(this.R.RigidBodyDesc.fixed().setTranslation(spec.base.x, spec.base.y, spec.base.z));
      const body = this.world.createRigidBody(this.R.RigidBodyDesc.dynamic().setTranslation(centre.x, centre.y, centre.z)
        .setCanSleep(true).setCcdEnabled(true).setLinearDamping(0.8).setAngularDamping(0.8));
      body.lockTranslations(true, false);
      body.lockRotations(true, false);
      this.world.createCollider(this.R.ColliderDesc.cylinder(spec.height / 2, 0.05)
        .setMass(12).setFriction(0.7).setActiveEvents(this.R.ActiveEvents.CONTACT_FORCE_EVENTS)
        .setCollisionGroups(groups(GROUP.DYNAMIC, GROUP.STATIC_SOLID | GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC)), body);
      const joint = this.R.JointData.fixed({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 0, w: 1 },
        { x: 0, y: -spec.height / 2, z: 0 }, { x: 0, y: 0, z: 0, w: 1 });
      const attached = this.world.createImpulseJoint(joint, anchor, body, true);
      this.poles.set(spec.id, { spec, body, anchor, joint: attached, brokenAt: null, brokeNow: false });
      body.sleep();
      if (this.brokenPoles.has(spec.id)) {
        this.breakPole(spec.id, false);
        body.setRotation({ x: 0, y: 0, z: Math.SQRT1_2, w: Math.SQRT1_2 }, false);
        body.setTranslation({ x: spec.base.x + spec.height / 2, y: spec.base.y + 0.05, z: spec.base.z }, false);
        body.setBodyType(this.R.RigidBodyType.Fixed, true);
      }
    }
  }

  breakPole(id: string, local = false) {
    this.brokenPoles.add(id);
    const pole = this.poles.get(id);
    if (!pole || pole.brokenAt !== null) return;
    if (pole.joint) this.world.removeImpulseJoint(pole.joint, true);
    pole.joint = null;
    pole.body.lockTranslations(false, true);
    pole.body.lockRotations(false, true);
    pole.brokenAt = this.tick;
    pole.brokeNow = local;
    pole.body.wakeUp();
    pole.body.applyImpulse({ x: 0.7, y: 0.1, z: 0.7 }, true);
    pole.body.applyTorqueImpulse({ x: 0, y: 0, z: 8 }, true);
  }

  poleStates(brokenOnly = false): PoleState[] {
    return [...this.poles].filter(([, p]) => !brokenOnly || p.brokenAt !== null).map(([id, p]) => {
      const resting = p.body.isSleeping() || p.body.bodyType() === this.R.RigidBodyType.Fixed;
      const old = this.poleSnapshots.get(id);
      if (brokenOnly && old && resting && old.resting && !p.brokeNow) return old.state;
      const pos = p.body.translation(), rot = p.body.rotation();
      const state = { id, pos: { x: pos.x, y: pos.y, z: pos.z },
        rot: { x: rot.x, y: rot.y, z: rot.z, w: rot.w }, broken: p.brokenAt !== null, brokeNow: p.brokeNow };
      p.brokeNow = false;
      if (brokenOnly) this.poleSnapshots.set(id, { state, resting });
      return state;
    });
  }

  // ------------------------------------------------------------------ stepping
  /** Advances real time dt with fixed 60 Hz substeps. Returns the number of substeps run. */
  step(dt: number, character: CharacterInput, vehicle: VehicleInput | null): number {
    this.jumpPending ||= character.jump;
    this.acc = Math.min(this.acc + Math.max(0, dt), MAX_SUBSTEPS * DT);
    let n = 0;
    while (this.acc >= DT - 1e-9 && n < MAX_SUBSTEPS) {
      // This decision belongs to the worker, immediately before every fixed substep: main-thread
      // braking cannot stop gravity, an evicted parked car, or a late input after a flight toggle.
      let safeCharacter: CharacterInput | null = { ...character, jump: this.jumpPending };
      if (this.charCollider && this.charEnabled) {
        const p = this.charCollider.translation();
        const covered = collisionCovers(this.residentCells, p.x, p.z,
          p.x + character.vx * DT, p.z + character.vz * DT, CHARACTER.radius + CHARACTER.skin);
        if (!covered) {
          // Flight can cross unbuilt areas, but cannot descend through their visible ground.
          safeCharacter = character.fly ? { ...safeCharacter, fly: { vy: Math.max(0, character.fly.vy) } } : null;
          this.charVy = 0; this.charVel = { x: 0, y: 0, z: 0 }; this.charGrounded = false;
        }
      }
      for (const [id, car] of this.vehicles) {
        const p = car.body.translation(), v = car.body.linvel();
        const radius = this.vehicleRadii.get(id)!;
        const covered = collisionCovers(this.residentCells, p.x, p.z, p.x + v.x * DT, p.z + v.z * DT, radius);
        if (!covered && !this.heldVehicles.has(id)) {
          car.body.setLinvel({ x: 0, y: 0, z: 0 }, false);
          car.body.setAngvel({ x: 0, y: 0, z: 0 }, false);
          car.body.setBodyType(this.R.RigidBodyType.Fixed, false);
          this.heldVehicles.add(id);
          this.vehicleSnapshots.delete(id);
        } else if (covered && this.heldVehicles.delete(id)) {
          car.body.setBodyType(this.R.RigidBodyType.Dynamic, true);
          car.body.wakeUp();
          this.vehicleSnapshots.delete(id);
        }
      }
      this.fixedStep(safeCharacter, vehicle);
      this.jumpPending = false;
      this.acc -= DT;
      n++;
    }
    return n;
  }

  /** Low-level solver step, also used by standalone synthetic collider fixtures. Runtime uses step(). */
  fixedStep(character: CharacterInput | null, vehicle: VehicleInput | null) {
    this.previousCharacter = this.characterState();
    this.previousVehicle = this.vehicleState();
    this.tick++;
    if (character && this.cc && this.charEnabled) this.stepCharacter(character);
    if (this.charEnabled && this.charCollider) {
      const feet = this.charCollider.translation();
      for (const [id, car] of this.vehicles) {
        const pos = car.body.translation();
        const distance = Math.hypot(pos.x - feet.x, pos.z - feet.z);
        if (distance < 6 && !this.nearbyVehicles.has(id)) {
          this.nearbyVehicles.add(id);
          car.body.wakeUp();
        } else if (distance > 8) this.nearbyVehicles.delete(id);
      }
    }
    const carSpeeds = new Map<number, { speed: number; local: boolean }>();
    for (const [id, car] of this.vehicles) {
      if (!this.heldVehicles.has(id)) car.step(DT, id === this.drivenId ? vehicle : null);
      const v = car.body.linvel();
      carSpeeds.set(car.body.handle, { speed: Math.hypot(v.x, v.z), local: id === this.drivenId });
    }
    this.world.step(this.poleEvents);
    for (const [id, car] of this.vehicles) if (!this.heldVehicles.has(id)) car.afterStep(DT);
    const poleColliders = new Map<number, string>();
    for (const [id, pole] of this.poles) if (pole.brokenAt === null) poleColliders.set(pole.body.collider(0).handle, id);
    this.poleEvents.drainContactForceEvents(event => {
      const a = event.collider1(), b = event.collider2();
      const id = poleColliders.get(a) ?? poleColliders.get(b);
      if (!id || event.totalForceMagnitude() * DT < 1) return;
      const other = this.world.getCollider(poleColliders.has(a) ? b : a);
      const car = other?.parent() && carSpeeds.get(other.parent()!.handle);
      if (car && car.speed >= 10 / 3.6) this.breakPole(id, car.local);
    });
    for (const pole of this.poles.values()) {
      if (pole.brokenAt !== null) {
        if (this.tick - pole.brokenAt >= 20 / DT && pole.body.bodyType() !== this.R.RigidBodyType.Fixed)
          pole.body.setBodyType(this.R.RigidBodyType.Fixed, true);
        continue;
      }
    }
  }

  // ------------------------------------------------------------------ queries
  cameraCast(origin: V3, dir: V3, distance: number): number {
    const hit = this.world.castShape(origin, { x: 0, y: 0, z: 0, w: 1 }, dir,
      new this.R.Ball(0.3), 0, distance, true, this.R.QueryFilterFlags.EXCLUDE_SENSORS,
      groups(0xff00, GROUP.STATIC_SOLID));
    return hit ? Math.max(0, hit.time_of_impact - 0.03) : distance;
  }

  raycast(origin: V3, dir: V3, maxToi: number, filter: RayFilter = 'solid'): RayHit | null {
    // Treads are query-only and can be below the KCC slope: prefer them over the solid ramp.
    if (filter === 'foot') {
      const tread = this.raycast(origin, dir, maxToi, 'query');
      if (tread?.material === MAT.stair_tread_helper) return tread;
      return this.raycast(origin, dir, maxToi, 'solid');
    }
    const R = this.R;
    const l = Math.hypot(dir.x, dir.y, dir.z) || 1;
    const d = { x: dir.x / l, y: dir.y / l, z: dir.z / l };
    const ray = new R.Ray(origin, d);
    const mask = filter === 'solid' ? GROUP.STATIC_SOLID : filter === 'query' ? GROUP.STATIC_QUERY : filter === 'water' ? GROUP.STATIC_SENSOR
      : GROUP.STATIC_SOLID | GROUP.STATIC_QUERY | GROUP.VEHICLE;
    const flags = filter === 'water' ? R.QueryFilterFlags.EXCLUDE_SOLIDS : R.QueryFilterFlags.EXCLUDE_SENSORS;
    const hit = this.world.castRayAndGetNormal(ray, maxToi, true, flags, groups(0xff00, mask), this.charCollider ?? undefined);
    if (!hit) return null;
    const info = this.colliderInfo.get(hit.collider.handle);
    let material = info?.mat ?? 0;
    const toi = hit.timeOfImpact;
    const px = origin.x + d.x * toi, py = origin.y + d.y * toi, pz = origin.z + d.z * toi;
    if (info?.triMat && info.locator) {
      const t = info.locator.locate(px, py, pz);
      if (t >= 0) material = info.triMat[t];
    }
    return { toi, point: { x: px, y: py, z: pz }, normal: { x: hit.normal.x, y: hit.normal.y, z: hit.normal.z }, material, tile: info?.tile ?? null };
  }

  totals() {
    let tris = 0, bytes = 0, curbTris = 0;
    for (const t of this.tiles.values()) { tris += t.stats.tris; bytes += t.stats.bytes; curbTris += t.stats.curbTris ?? 0; }
    return { tris, colBytes: bytes, estWasmBytes: (tris + curbTris) * 135 }; // ~135 B/tri measured (engine.md 2.2)
  }
}

/** World heights spanned by a tile's triangles and primitives. */
function heightRange(col: ColTile): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (let k = 1; k < col.positions.length; k += 3) { lo = Math.min(lo, col.positions[k]); hi = Math.max(hi, col.positions[k]); }
  for (const p of col.prims) { lo = Math.min(lo, p.center[1]); hi = Math.max(hi, p.center[1]); }
  return Number.isFinite(lo) ? [lo + col.origin[1], hi + col.origin[1]] : [col.origin[1], col.origin[1]];
}
