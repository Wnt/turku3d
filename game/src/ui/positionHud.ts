import * as THREE from 'three/webgpu';
import { engineToEN, VISUAL_L0, TRUE_NORTH_GRID_BEARING_DEG } from '../core/origin';
import { enToLatLon } from '../core/geographic';
import { CITY } from '../core/city';
import { devToolsEnabled } from '../core/localOnly';
import type { Input } from '../input';
import type { VisualTiles } from '../world/tiles';
import type { V3 } from '../core/origin';

const STORAGE_KEY = 'turku3d.position-hud.v1';
const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function buildingTarget(hit?: THREE.Intersection): string {
  for (let o: THREE.Object3D | null = hit?.object ?? null; o; o = o.parent) {
    const data = o.userData;
    const offset = (hit?.faceIndex ?? -1) * 3;
    const range = hit?.instanceId != null ? data.instance_objects?.[hit.instanceId]
      : data.objects?.find((r: { start: number; count: number }) => offset >= r.start && offset < r.start + r.count);
    const d = range ?? data;
    if (d.building_id != null) return [d.building_id, d.address].filter(Boolean).join(' · ');
    // A batch identity describes a draw call, never a building.
    if (d.layer === 'buildings' && !data.objects && !data.instance_objects)
      return [d.object_id ?? o.name, d.address].filter(Boolean).join(' · ');
  }
  return '';
}

/** Reference views of the camera position: Google 3D and Street View, Apple Look Around and 3D (heading/pitch where the
 *  service takes them). Opened in a new tab from the position readout; nothing is fetched by the game. */
export function referenceLinks(lat: number, lon: number, groundH: number, heading: number, pitch: number) {
  const ll = `${lat.toFixed(6)},${lon.toFixed(6)}`;
  const h = Math.round(heading), p = Math.round(pitch);
  const tilt = Math.min(85, Math.max(0, Math.round(90 + pitch)));
  const altitude = Math.round(Math.max(60, groundH + 80));
  return [
    { label: 'Google 3D', url: `https://www.google.com/maps/@${ll},${altitude}a,35y,${h}h,${tilt}t/data=!3m1!1e3` },
    { label: 'Street View', url: `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${ll}&heading=${h}&pitch=${p}&fov=90` },
    { label: 'Look Around', url: `https://maps.apple.com/look-around?coordinate=${ll}` },
    { label: 'Apple 3D', url: `https://maps.apple.com/frame?map=explore&center=${ll}&distance=250&pitch=${Math.min(75, Math.max(0, 90 + p))}&heading=${h}` },
  ];
}

export function positionReadout(position: THREE.Vector3, direction: THREE.Vector3, target = '') {
  const { E, N, H } = engineToEN(position.x, position.y, position.z);
  const { lat, lon } = enToLatLon(E, N);
  const heading = (Math.atan2(direction.x, -direction.z) * 180 / Math.PI - TRUE_NORTH_GRID_BEARING_DEG + 360) % 360;
  const degrees = Math.round(heading) % 360;
  const pitch = Math.atan2(direction.y, Math.hypot(direction.x, direction.z)) * 180 / Math.PI;
  return [
    `Camera ${lat.toFixed(6)}, ${lon.toFixed(6)}`,
    `${CITY.crs} E ${E.toFixed(2)} N ${N.toFixed(2)} · H ${H.toFixed(2)} m ${CITY.heightDatum}`,
    `${degrees}° ${CARDINALS[Math.round(heading / 45) % 8]} · pitch ${pitch.toFixed(1)}° · t0_${Math.floor(position.x / VISUAL_L0)}_${Math.floor(position.z / VISUAL_L0)}`,
    ...(target ? [`Centre: ${target}`] : []),
  ];
}

/** Nearest solid collision along a ray (play: the physics worker's Rapier query); null when there is none or the
 *  collision there is not resident. */
export type CollisionProbe = (origin: V3, direction: V3, far: number) => Promise<{ toi: number } | null>;

/** Layers whose meshes can name a building (buildingTarget). */
const BUILDING_LAYERS = new Set(['buildings', 'buildings_outer', 'massing']);
const FAR = 300;
/** A collision hit names the building whose bounds hold it (padded by SLACK) and whose visual surface lies from AHEAD
 *  in front of it (pitched roofs, overhangs, balconies over simplified collision) to SLACK behind it (m). */
const SLACK = 1.5, AHEAD = 5;
/** Look-at naming probes at most this often (ms), and only after the camera moved or the resident tiles changed. */
const NAMING_MS = 500;

type RangedGeometry = THREE.BufferGeometry & { userData: { buildingRangeBounds?: Float32Array } };

/** Developer-only camera pose and photo controls, independent of the debug HUD. */
export class PositionHud {
  readonly element = Object.assign(document.createElement('div'), { id: 'position-hud' });
  /** Clickable while the pointer is free (pause menu); rebuilt only when the rounded pose changes. */
  private readonly links = Object.assign(document.createElement('div'), { className: 'position-links' });
  private linksKey = '';
  visible = true;
  characterHidden = false;
  /** The look-at naming: the building at the screen centre in the readout (developer menu; off by default). Off, it
   *  does no work at all. Always on it raycast render meshes of 3.5-4.7 M triangles twice a second in Jonni's
   *  2026-11-05 session (135 MB allocated per 15 s). */
  naming = false;
  private lastUpdate = -Infinity;
  private direction = new THREE.Vector3();
  private ray = new THREE.Raycaster();
  private centre = new THREE.Vector2();
  private noticeUntil = 0;
  private notice = '';
  private bounds = new THREE.Box3();
  private point = new THREE.Vector3();
  private local = new THREE.Vector3();
  private hitPoint = new THREE.Vector3();
  private localRay = new THREE.Ray();
  private inverse = new THREE.Matrix4();
  private corner = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()] as const;
  private hits: THREE.Intersection[] = [];
  private candidates: THREE.Mesh[] = [];
  private queryOrigin = new THREE.Vector3();
  private queryDirection = new THREE.Vector3();
  private queryAt = -Infinity;
  private queryLoads = -1;
  private queryModels = -1;
  private queryVisibility = -1;
  private probing: Promise<void> | null = null;
  private target = '';
  private instanceBounds = new WeakMap<THREE.InstancedMesh, { matrix: THREE.InstancedBufferAttribute; version: number; count: number; sphere: THREE.Sphere }>();
  private nameable = new WeakMap<THREE.Object3D, boolean>();
  /** Look-at naming lookups run (tests). */
  visualQueries = 0;

  /** `probe` finds the nearest surface on collision; only the building meshes whose bounds hold that point are tested,
   *  and of merged building meshes only the building ranges whose bounds hold it (a one-level BVH, cached per geometry). */
  constructor(private camera: THREE.PerspectiveCamera, private tiles: VisualTiles, readonly enabled = devToolsEnabled(location),
    private visibilityVersion: () => number = () => 0, private probe?: CollisionProbe) {
    try { this.visible = localStorage.getItem(STORAGE_KEY) !== 'off'; } catch { /* Session default. */ }
    this.visible &&= this.enabled;
    if (this.links.style) this.links.style.pointerEvents = 'auto';
    document.body.append(this.element);
    this.element.hidden = !this.visible;
  }

  handle(input: Input, playing: boolean) {
    if (!this.enabled || !playing) return;
    if (input.consume('positionHud')) this.toggle();
    if (input.consume('photoCharacter')) this.characterHidden = !this.characterHidden;
  }

  toggle() {
    if (!this.enabled) return;
    this.visible = !this.visible;
    this.element.hidden = !this.visible;
    this.lastUpdate = -Infinity;
    this.queryAt = -Infinity;
    try { localStorage.setItem(STORAGE_KEY, this.visible ? 'on' : 'off'); } catch { /* Keep session state. */ }
  }

  setNaming(on: boolean) {
    this.naming = on && this.enabled;
    this.target = ''; this.queryAt = -Infinity; this.lastUpdate = -Infinity;
  }

  private lines(now: number, force = false) {
    this.camera.updateWorldMatrix(true, false);
    this.camera.getWorldDirection(this.direction);
    if (this.naming && this.probe && !this.probing) {
      this.ray.setFromCamera(this.centre, this.camera);
      const visibility = this.visibilityVersion();
      const origin = this.ray.ray.origin, direction = this.ray.ray.direction;
      const changed = this.queryLoads !== this.tiles.loadedModels || this.queryModels !== this.tiles.models.size
        || this.queryVisibility !== visibility;
      const moved = !this.queryOrigin.equals(origin) || !this.queryDirection.equals(direction);
      if (force || ((moved || changed) && now - this.queryAt >= NAMING_MS)) {
        this.remember(now, visibility);
        const o = origin.clone(), d = direction.clone();
        this.probing = this.probe(o, d, FAR).then(hit => { this.target = hit ? this.nameAt(o, d, hit.toi) : ''; }, () => {})
          .finally(() => { this.probing = null; });
      }
    }
    return positionReadout(this.camera.position, this.direction, this.naming ? this.target : '');
  }

  private remember(now: number, visibility: number) {
    this.queryAt = now;
    this.queryLoads = this.tiles.loadedModels;
    this.queryModels = this.tiles.models.size;
    this.queryVisibility = visibility;
    this.queryOrigin.copy(this.ray.ray.origin);
    this.queryDirection.copy(this.ray.ray.direction);
  }

  /** The building at a collision hit: the nearest visual surface from AHEAD in front of the hit to SLACK behind it,
   *  among the building meshes whose bounds hold the hit point. */
  private nameAt(origin: THREE.Vector3, direction: THREE.Vector3, toi: number): string {
    this.visualQueries++;
    this.point.copy(direction).multiplyScalar(toi).add(origin);
    this.ray.set(origin, direction); this.ray.near = Math.max(0, toi - AHEAD); this.ray.far = toi + SLACK;
    this.candidates.length = 0;
    for (const model of this.tiles.models) model.traverseVisible(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || o.userData.shadowBatch || !this.ray.layers.test(mesh.layers) || !this.names(mesh)) return;
      if (this.meshBounds(mesh).expandByScalar(SLACK).containsPoint(this.point)) this.candidates.push(mesh);
    });
    let best: { distance: number; object: THREE.Object3D; faceIndex?: number | null; instanceId?: number } | undefined;
    for (const mesh of this.candidates) {
      const ranges = mesh.userData.objects as { start: number; count: number }[] | undefined;
      if (ranges?.length && !(mesh as THREE.InstancedMesh).isInstancedMesh) {
        const hit = this.rangeHit(mesh, ranges);
        if (hit && (!best || hit.distance < best.distance)) { best = hit; this.ray.far = hit.distance; }
        continue;
      }
      this.hits.length = 0;
      const instance = mesh as THREE.InstancedMesh, bounds = this.instanceBounds.get(instance);
      if (bounds) {
        // Raycast needs the same fresh bounds. Preserve any conservative sphere installed by the renderer/LOD owner;
        // never tighten its render culling.
        const sphere = instance.boundingSphere;
        try { instance.boundingSphere = bounds.sphere; mesh.raycast(this.ray, this.hits); }
        finally { instance.boundingSphere = sphere; }
      } else mesh.raycast(this.ray, this.hits);
      for (const hit of this.hits) if (hit.distance >= this.ray.near && (!best || hit.distance < best.distance)) {
        best = hit; this.ray.far = hit.distance;
      }
    }
    const target = buildingTarget(best as THREE.Intersection | undefined);
    // Intersections and candidates hold meshes: a hidden readout must not retain evicted tiles.
    this.hits.length = this.candidates.length = 0;
    this.ray.near = 0;
    return target;
  }

  /** The nearest hit on a merged building mesh, testing only the triangles of the ranges whose bounds hold the point. */
  private rangeHit(mesh: THREE.Mesh, ranges: readonly { start: number; count: number }[]) {
    const geometry = mesh.geometry as RangedGeometry, boxes = this.rangeBounds(geometry, ranges);
    const position = geometry.attributes.position, index = geometry.index;
    this.inverse.copy(mesh.matrixWorld).invert();
    this.local.copy(this.point).applyMatrix4(this.inverse);
    this.localRay.copy(this.ray.ray).applyMatrix4(this.inverse);
    const [a, b, c] = this.corner;
    let best: { distance: number; object: THREE.Object3D; faceIndex: number } | undefined;
    for (let r = 0; r < ranges.length; r++) {
      const k = 6 * r, p = this.local;
      if (p.x < boxes[k] - SLACK || p.y < boxes[k + 1] - SLACK || p.z < boxes[k + 2] - SLACK
        || p.x > boxes[k + 3] + SLACK || p.y > boxes[k + 4] + SLACK || p.z > boxes[k + 5] + SLACK) continue;
      const { start, count } = ranges[r];
      for (let i = start; i + 2 < start + count; i += 3) {
        const ia = index ? index.getX(i) : i, ib = index ? index.getX(i + 1) : i + 1, ic = index ? index.getX(i + 2) : i + 2;
        a.fromBufferAttribute(position, ia); b.fromBufferAttribute(position, ib); c.fromBufferAttribute(position, ic);
        if (!this.localRay.intersectTriangle(a, b, c, false, this.hitPoint)) continue;
        const distance = this.hitPoint.applyMatrix4(mesh.matrixWorld).distanceTo(this.ray.ray.origin);
        if (distance < this.ray.near || distance > this.ray.far || (best && distance >= best.distance)) continue;
        best = { distance, object: mesh, faceIndex: i / 3 };
      }
    }
    return best;
  }

  /** Local-space bounds (min xyz, max xyz) of each building range of a merged geometry, computed once. */
  private rangeBounds(geometry: RangedGeometry, ranges: readonly { start: number; count: number }[]) {
    let boxes = geometry.userData.buildingRangeBounds;
    if (boxes && boxes.length === 6 * ranges.length) return boxes;
    boxes = geometry.userData.buildingRangeBounds = new Float32Array(6 * ranges.length);
    const position = geometry.attributes.position, index = geometry.index;
    for (let r = 0; r < ranges.length; r++) {
      let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
      for (let i = ranges[r].start; i < ranges[r].start + ranges[r].count; i++) {
        const v = index ? index.getX(i) : i, x = position.getX(v), y = position.getY(v), z = position.getZ(v);
        if (x < x0) x0 = x; if (y < y0) y0 = y; if (z < z0) z0 = z;
        if (x > x1) x1 = x; if (y > y1) y1 = y; if (z > z1) z1 = z;
      }
      boxes.set([x0, y0, z0, x1, y1, z1], 6 * r);
    }
    return boxes;
  }

  /** World bounds of a mesh (instanced meshes: of all instances, refreshed when their matrices change), in `this.bounds`. */
  private meshBounds(mesh: THREE.Mesh): THREE.Box3 {
    if ((mesh as THREE.InstancedMesh).isInstancedMesh) {
      const instance = mesh as THREE.InstancedMesh;
      let bounds = this.instanceBounds.get(instance);
      if (!instance.boundingBox || !bounds || bounds.matrix !== instance.instanceMatrix
        || bounds.version !== instance.instanceMatrix.version || bounds.count !== instance.count) {
        instance.computeBoundingBox();
        if (!bounds) {
          bounds = { matrix: instance.instanceMatrix, version: -1, count: -1, sphere: new THREE.Sphere() };
          this.instanceBounds.set(instance, bounds);
        }
        bounds.matrix = instance.instanceMatrix; bounds.version = instance.instanceMatrix.version; bounds.count = instance.count;
        instance.boundingBox!.getBoundingSphere(bounds.sphere);
      }
      this.bounds.copy(instance.boundingBox!);
    } else {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      this.bounds.copy(mesh.geometry.boundingBox!);
    }
    return this.bounds.applyMatrix4(mesh.matrixWorld);
  }

  /** Whether a mesh can name a building (buildingTarget): a building layer or a building id on it or an ancestor. */
  private names(mesh: THREE.Object3D): boolean {
    let known = this.nameable.get(mesh);
    if (known !== undefined) return known;
    known = false;
    for (let o: THREE.Object3D | null = mesh; o && !known; o = o.parent) {
      const d = o.userData;
      known = d.building_id != null || d.placementId != null || BUILDING_LAYERS.has(d.layer)
        || !!d.objects?.some((r: { building_id?: unknown }) => r.building_id != null)
        || !!d.instance_objects?.some((r: { building_id?: unknown } | null) => r?.building_id != null);
    }
    this.nameable.set(mesh, known);
    return known;
  }

  update(now: number, playing: boolean) {
    this.element.hidden = !this.enabled || !playing || !this.visible;
    if (this.element.hidden || now - this.lastUpdate < 500) return;
    this.lastUpdate = now;
    this.element.textContent = [...this.lines(now), ...(now < this.noticeUntil ? [this.notice] : [])].join('\n');
    // textContent drops children; re-attach the same link nodes (they are rebuilt only when the pose moves).
    if (typeof this.element.append === 'function') { this.updateLinks(); this.element.append(this.links); }
  }

  private updateLinks() {
    const { E, N, H } = engineToEN(this.camera.position.x, this.camera.position.y, this.camera.position.z);
    const { lat, lon } = enToLatLon(E, N);
    const heading = (Math.atan2(this.direction.x, -this.direction.z) * 180 / Math.PI - TRUE_NORTH_GRID_BEARING_DEG + 360) % 360;
    const pitch = Math.atan2(this.direction.y, Math.hypot(this.direction.x, this.direction.z)) * 180 / Math.PI;
    const key = `${lat.toFixed(5)},${lon.toFixed(5)},${Math.round(heading / 5)},${Math.round(pitch / 5)}`;
    if (key === this.linksKey) return;
    this.linksKey = key;
    this.links.replaceChildren(...referenceLinks(lat, lon, H, heading, pitch).flatMap((link, i) => {
      const a = Object.assign(document.createElement('a'), { href: link.url, target: '_blank', rel: 'noopener noreferrer', textContent: link.label });
      a.style.color = '#9fd3ff';
      return i ? [document.createTextNode(' · '), a] : [a];
    }));
  }

  /** Called directly from the keyboard gesture so Clipboard API activation is retained. */
  async copy() {
    if (!this.enabled) return;
    this.lines(performance.now(), true);
    await this.probing;
    const text = positionReadout(this.camera.position, this.direction, this.target).join(' · ');
    try {
      await navigator.clipboard.writeText(text);
      this.notice = 'Position copied';
    } catch { this.notice = 'Copy failed — allow clipboard access'; }
    this.noticeUntil = performance.now() + 2500;
    this.lastUpdate = -Infinity;
  }
}
