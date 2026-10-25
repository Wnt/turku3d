// Rapier world used by the physics worker (and directly by the Node unit tests).
// Everything is in engine world-local coordinates; each .col tile's float64 origin becomes its collider translation.
import type * as RAPIER_NS from '@dimforge/rapier3d';
import type { V3 } from '../core/origin';
import { GROUP, materialClass, MATERIALS, MAT } from '../shared/col/materials';
import { readCol, COL_FLAG_FIX_INTERNAL_EDGES, type ColTile } from './colTile';
import { TriLocator } from './triLocator';
import { CHARACTER } from './constants';
import { Vehicle } from '../vehicle/vehicle';
import { CurbQueries } from '../vehicle/curbQueries';
import { isCurbMaterial } from '../vehicle/curbStrike';
export { CHARACTER };
import type { CharacterInput, CharacterState, RayFilter, RayHit, TileStats, VehicleInput, VehicleState } from './protocol';

type R = typeof RAPIER_NS;
export const DT = 1 / 60;
const MAX_SUBSTEPS = 5;

/** Rapier interaction groups: high 16 bits membership, low 16 bits filter. */
const groups = (membership: number, filter: number) => ((membership & 0xffff) << 16) | (filter & 0xffff);

interface ColliderInfo { tile: string; triMat: Uint8Array | null; mat: number; locator: TriLocator | null }
interface LoadedTile { stats: TileStats; colliders: RAPIER_NS.Collider[] }

export class PhysicsWorld {
  readonly world: RAPIER_NS.World;
  private tiles = new Map<string, LoadedTile>();
  private colliderInfo = new Map<number, ColliderInfo>();
  private acc = 0;
  tick = 0;
  previousCharacter: CharacterState | null = null;
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
  vehicle: Vehicle | null = null;
  private curbQueries: CurbQueries;

  constructor(private readonly R: R) {
    this.world = new R.World({ x: 0, y: -9.81, z: 0 });
    this.world.timestep = DT;
    this.curbQueries = new CurbQueries(R, this.world);
  }

  // ------------------------------------------------------------------ tiles
  async loadTile(id: string, buffer: ArrayBuffer): Promise<TileStats> {
    if (this.tiles.has(id)) this.unloadTile(id);
    const t0 = performance.now();
    const col = await readCol(buffer);
    const colliders = this.buildTile(id, col);
    const stats: TileStats = { id, tris: col.indices.length / 3, prims: col.prims.length, bytes: buffer.byteLength, buildMs: +(performance.now() - t0).toFixed(2), colliders: colliders.length, curbTris: colliders.reduce((n, c) => n + this.curbQueries.triangleCount(c.handle), 0) };
    this.tiles.set(id, { stats, colliders });
    return stats;
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
      const curbIdx: number[] = [], curbMat: number[] = [];
      tris.forEach((t, k) => {
        if (isCurbMaterial(triMat[k])) { curbIdx.push(...col.indices.subarray(t * 3, t * 3 + 3)); curbMat.push(triMat[k]); }
      });
      if (curbIdx.length) {
        // Compact the helper: do not duplicate the full ground vertex buffer in WASM.
        const remap = new Map<number, number>(), vertices: number[] = [];
        const indices = Uint32Array.from(curbIdx, index => {
          if (!remap.has(index)) { remap.set(index, remap.size); vertices.push(...col.positions.subarray(index * 3, index * 3 + 3)); }
          return remap.get(index)!;
        });
        const positions = Float32Array.from(vertices);
        const locator = new TriLocator(positions, indices, col.origin);
        out.push(this.curbQueries.add(c, new R.TriMesh(positions, indices), point => {
          const t = locator.locate(point.x, point.y, point.z, 0.04);
          return t < 0 ? null : { material: curbMat[t], top: locator.top(t) };
        }));
      }
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
      if (isCurbMaterial(p.material)) out.push(this.curbQueries.add(c, c.shape, point => {
        // Sample the actual rotated primitive, not its axis-aligned bounds.
        const ray = new R.Ray({ x: point.x, y: point.y + 10, z: point.z }, { x: 0, y: -1, z: 0 });
        const hit = c.castRay(ray, 20, true);
        return hit < 0 ? null : { material: p.material, top: point.y + 10 - hit };
      }));
      out.push(c);
    }
    return out;
  }

  private applyClass(desc: RAPIER_NS.ColliderDesc, cls: 'solid' | 'query' | 'sensor', friction: number) {
    if (cls === 'solid') desc.setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00)).setFriction(friction);
    else if (cls === 'query') desc.setCollisionGroups(groups(GROUP.STATIC_QUERY, 0xff00)).setSolverGroups(0);
    else desc.setSensor(true).setCollisionGroups(groups(GROUP.STATIC_SENSOR, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC));
  }

  unloadTile(id: string): boolean {
    const t = this.tiles.get(id);
    if (!t) return false;
    for (const c of t.colliders) { this.colliderInfo.delete(c.handle); this.curbQueries.remove(c.handle); this.world.removeCollider(c, false); }
    this.tiles.delete(id);
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
      cc.enableAutostep(CHARACTER.autostepHeight, CHARACTER.autostepMinWidth, false);
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
    // One combined move per 60 Hz step (the tools/engine-probe scheme, also the validator default
    // tools/validator/src/kcc.ts 'probe'): while grounded the vertical speed is a small constant -0.5 m/s
    // (-8 mm/frame), full gravity applies only in the air. Measured with Rapier 0.21 + autostep 0.22/0.15:
    // climbs steps <= 0.27 m and is blocked at >= 0.30 m at 1.5-5 m/s. The literal "horizontal move, then a
    // separate gravity move" split climbs only <= 0.22 m at 1.5 m/s (a 25 cm curb blocks; Task 1.11c fails).
    if (this.charGrounded && input.jump) this.charVy = CHARACTER.jumpSpeed;
    else if (this.charGrounded && this.charVy <= 0) this.charVy = CHARACTER.groundedVy;
    else this.charVy = Math.max(this.charVy - CHARACTER.gravity * DT, -50);
    const wanted = this.charVy * DT;
    cc.computeColliderMovement(col, { x: input.vx * DT, y: wanted, z: input.vz * DT }, flags, filter);
    const m = cc.computedMovement();
    const p = { x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z };
    col.setTranslation(p);
    this.charGrounded = cc.computedGrounded();
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
  spawnVehicle(pos: V3, yaw: number) {
    if (this.vehicle) { this.vehicle.reset(pos, yaw); return; }
    this.vehicle = new Vehicle(this.R, this.world, pos, yaw, this.curbQueries);
  }

  resetVehicle(pos: V3, yaw: number) { this.vehicle?.reset(pos, yaw); }
  vehicleState(): VehicleState | null { return this.vehicle?.state() ?? null; }

  // ------------------------------------------------------------------ stepping
  /** Advances real time dt with fixed 60 Hz substeps. Returns the number of substeps run. */
  step(dt: number, character: CharacterInput, vehicle: VehicleInput | null): number {
    this.jumpPending ||= character.jump;
    this.acc = Math.min(this.acc + Math.max(0, dt), MAX_SUBSTEPS * DT);
    let n = 0;
    while (this.acc >= DT - 1e-9 && n < MAX_SUBSTEPS) {
      this.fixedStep({ ...character, jump: this.jumpPending }, vehicle);
      this.jumpPending = false;
      this.acc -= DT;
      n++;
    }
    return n;
  }

  fixedStep(character: CharacterInput, vehicle: VehicleInput | null) {
    this.previousCharacter = this.characterState();
    this.tick++;
    if (this.cc && this.charEnabled) this.stepCharacter(character);
    this.vehicle?.step(DT, vehicle);
    this.world.step();
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
    const mask = filter === 'solid' ? GROUP.STATIC_SOLID : filter === 'query' ? GROUP.STATIC_QUERY : GROUP.STATIC_SOLID | GROUP.STATIC_QUERY | GROUP.VEHICLE;
    const hit = this.world.castRayAndGetNormal(ray, maxToi, true, R.QueryFilterFlags.EXCLUDE_SENSORS, groups(0xff00, mask), this.charCollider ?? undefined);
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

