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
/** Visual raycasts without collision: at most this often (ms), and only after the camera moved this far (m). */
const FALLBACK_MS = 1000, FALLBACK_MOVE = 1;

/** Developer-only camera pose and photo controls, independent of the debug HUD. */
export class PositionHud {
  readonly element = Object.assign(document.createElement('div'), { id: 'position-hud' });
  /** Clickable while the pointer is free (pause menu); rebuilt only when the rounded pose changes. */
  private readonly links = Object.assign(document.createElement('div'), { className: 'position-links' });
  private linksKey = '';
  visible = true;
  characterHidden = false;
  private lastUpdate = -Infinity;
  private direction = new THREE.Vector3();
  private ray = new THREE.Raycaster();
  private centre = new THREE.Vector2();
  private noticeUntil = 0;
  private notice = '';
  private bounds = new THREE.Box3();
  private point = new THREE.Vector3();
  private hits: THREE.Intersection[] = [];
  private candidates: { mesh: THREE.Mesh; distance: number }[] = [];
  private queryOrigin = new THREE.Vector3();
  private queryDirection = new THREE.Vector3();
  private queryAt = -Infinity;
  private queryLoads = -1;
  private queryModels = -1;
  private queryVisibility = -1;
  private probing: Promise<void> | null = null;
  private fallbackAt = -Infinity;
  private fallbackOrigin = new THREE.Vector3(Infinity, 0, 0);
  private fallbackDirection = new THREE.Vector3();
  private target = '';
  private instanceBounds = new WeakMap<THREE.InstancedMesh, { matrix: THREE.InstancedBufferAttribute; version: number; count: number; sphere: THREE.Sphere }>();
  private naming = new WeakMap<THREE.Object3D, boolean>();
  /** Visual raycasts run (QA / tests: the collision path should need almost none). */
  visualQueries = 0;

  /** `probe` finds the nearest surface on collision (play); then only building meshes whose bounds hold that point
   *  are raycast to name it. Without it (or beyond resident collision) the visual meshes are raycast, throttled. */
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

  private lines(now: number, force = false) {
    this.camera.updateWorldMatrix(true, false);
    this.camera.getWorldDirection(this.direction);
    this.ray.setFromCamera(this.centre, this.camera);
    const visibility = this.visibilityVersion();
    const origin = this.ray.ray.origin, direction = this.ray.ray.direction;
    const changed = this.queryLoads !== this.tiles.loadedModels || this.queryModels !== this.tiles.models.size
      || this.queryVisibility !== visibility;
    const moved = !this.queryOrigin.equals(origin) || !this.queryDirection.equals(direction);
    // The camera readout still refreshes at 2 Hz. An unchanged view reuses its centre target; bound freshness for
    // in-place geometry edits such as broken props.
    if (this.probe) {
      if (!this.probing && (force || ((moved || changed) && now - this.queryAt >= 500) || now - this.queryAt >= 2000)) {
        this.remember(now, visibility);
        const o = origin.clone(), d = direction.clone();
        this.probing = this.probe(o, d, FAR).then(hit => {
          // Nothing on collision: only a building beyond resident collision can be named.
          this.target = hit ? this.nameAt(o, d, hit.toi) : this.searchVisual(o, d, now, force, force || changed, true) ?? this.target;
        }, () => {}).finally(() => { this.probing = null; });
      }
    } else if (force || changed || now - this.queryAt >= 2000 || moved) {
      const refresh = force || changed || now - this.queryAt >= 2000;
      const target = this.searchVisual(origin, direction, now, force, refresh, false);
      if (target !== null) { this.remember(now, visibility); this.target = target; }
    }
    return positionReadout(this.camera.position, this.direction, this.target);
  }

  private remember(now: number, visibility: number) {
    this.queryAt = now;
    this.queryLoads = this.tiles.loadedModels;
    this.queryModels = this.tiles.models.size;
    this.queryVisibility = visibility;
    this.queryOrigin.copy(this.ray.ray.origin);
    this.queryDirection.copy(this.ray.ray.direction);
  }

  /** The building at a collision hit: only building meshes whose bounds hold the hit point are raycast. */
  private nameAt(origin: THREE.Vector3, direction: THREE.Vector3, toi: number): string {
    this.point.copy(direction).multiplyScalar(toi).add(origin);
    const at = this.point.clone();
    return this.raycastVisual(origin, direction, Math.max(0, toi - AHEAD), toi + SLACK,
      mesh => this.names(mesh) && this.meshBounds(mesh).expandByScalar(SLACK).containsPoint(at));
  }

  /** Without collision: bounding boxes first, then the nearest surface among the visible meshes (every one, or only
   *  building ones), at most once a second and after the camera moved or turned; null when skipped. Raycasting all
   *  resident triangles just to discard every hit except the first scales with the whole city. */
  private searchVisual(origin: THREE.Vector3, direction: THREE.Vector3, now: number, force: boolean, refresh: boolean,
    buildings: boolean): string | null {
    // Streaming, filter changes and the 2 s freshness refresh a still view; motion counts once past 1 m or 1°.
    if (!force && (now - this.fallbackAt < FALLBACK_MS || !refresh && this.fallbackOrigin.distanceTo(origin) <= FALLBACK_MOVE
      && this.fallbackDirection.angleTo(direction) < 0.02)) return null;
    this.fallbackAt = now; this.fallbackOrigin.copy(origin); this.fallbackDirection.copy(direction);
    return this.raycastVisual(origin, direction, 0, FAR, buildings ? mesh => this.names(mesh) : () => true);
  }

  private raycastVisual(origin: THREE.Vector3, direction: THREE.Vector3, near: number, far: number, keep: (mesh: THREE.Mesh) => boolean): string {
    this.visualQueries++;
    this.ray.set(origin, direction); this.ray.near = near; this.ray.far = far;
    this.candidates.length = 0;
    for (const model of this.tiles.models) model.traverseVisible(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || o.userData.shadowBatch || !this.ray.layers.test(mesh.layers) || !keep(mesh)) return;
      const bounds = this.meshBounds(mesh);
      if (!this.ray.ray.intersectBox(bounds, this.point)) return;
      const distance = bounds.containsPoint(this.ray.ray.origin) ? 0 : this.point.distanceTo(this.ray.ray.origin);
      if (distance <= this.ray.far) this.candidates.push({ mesh, distance });
    });
    this.candidates.sort((a, b) => a.distance - b.distance);
    let hit: THREE.Intersection | undefined;
    for (const { mesh, distance } of this.candidates) {
      if (distance > this.ray.far) break;
      this.hits.length = 0;
      const instance = mesh as THREE.InstancedMesh;
      const bounds = this.instanceBounds.get(instance);
      if (bounds) {
        // Raycast needs the same fresh bounds. Preserve any conservative sphere
        // installed by the renderer/LOD owner; never tighten its render culling.
        const sphere = instance.boundingSphere;
        try { instance.boundingSphere = bounds.sphere; mesh.raycast(this.ray, this.hits); }
        finally { instance.boundingSphere = sphere; }
      } else mesh.raycast(this.ray, this.hits);
      for (const candidate of this.hits) if (candidate.distance >= this.ray.near && (!hit || candidate.distance < hit.distance)) {
        hit = candidate;
        this.ray.far = hit.distance;
      }
    }
    const target = buildingTarget(hit);
    // Intersections and candidates hold meshes: a hidden readout must not retain evicted tiles.
    this.hits.length = this.candidates.length = 0;
    this.ray.near = 0;
    return target;
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
    let known = this.naming.get(mesh);
    if (known !== undefined) return known;
    known = false;
    for (let o: THREE.Object3D | null = mesh; o && !known; o = o.parent) {
      const d = o.userData;
      known = d.building_id != null || d.placementId != null || BUILDING_LAYERS.has(d.layer)
        || !!d.objects?.some((r: { building_id?: unknown }) => r.building_id != null)
        || !!d.instance_objects?.some((r: { building_id?: unknown } | null) => r?.building_id != null);
    }
    this.naming.set(mesh, known);
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
