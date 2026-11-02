import * as THREE from 'three/webgpu';
import { devToolsEnabled } from '../core/localOnly';
import type { Input } from '../input';
import type { CoarseTiles } from '../world/coarseTiles';
import { DevLayerRanges } from './devLayerRanges';
import { retireAfterFrame } from '../render/retirement';

const KEY = 'turku3d.dev-layers.v1';
type Data = Record<string, unknown>;
export const label = (value: string) => value.replace(/[_/-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

/** Use authored semantics before names; retain unknown layer/type names so additions need no UI update. */
export function layerPath(data: Data, name = ''): string | null {
  if (data.devLayer) return String(data.devLayer);
  if (data.shadowBatch) return 'Effects/Shadows';
  const layer = String(data.layer ?? '');
  const kind = ['prop', 'building', layer].includes(String(data.kind)) ? undefined : data.kind;
  const identity = String(data.object_id ?? name);
  const structureType = /^(bridge|tunnel):/.test(identity)
    ? identity.split(':').slice(2).find(part => /^[a-z][a-z_-]*$/.test(part)) : undefined;
  const type = String(data.prop_type ?? data.species ?? data.variant ?? data.surface ?? structureType ?? kind ?? data.material_key ?? layer);
  const hint = `${layer} ${type} ${name}`.toLowerCase();
  if (!layer && !data.devLayer) {
    if (/tyre-marks/.test(name)) return 'Effects/Skid Marks';
    if (/tyre-smoke/.test(name)) return 'Effects/Smoke';
    if (/^(player|remote-character)/.test(name)) return 'People/Players';
    if (/^(car|truck|scooter|driven\/)/.test(name)) return 'Vehicles/Local Vehicles';
    if (/^parked\//.test(name)) return 'Vehicles/Parked Cars';
    return null;
  }
  const group = /buildings|facade/.test(layer) ? 'Buildings'
    : /vegetation|forest|tree|bush/.test(layer) ? 'Vegetation'
    : /props|poles/.test(layer) ? 'Props'
    : /structure|bridge/.test(layer) || /tunnel|stair|fence/.test(type) ? 'Structures'
    : /ground|basic|water|river|bank|kerb|curb|terrain/.test(hint) ? 'Ground'
    : /wall/.test(hint) ? 'Structures' : label(layer);
  return `${group}/${label(type || layer)}`;
}

interface Filtered { ranges: DevLayerRanges; release: () => void }

/** Visual-only filters. Camera-layer masks preserve animation, LOD and gameplay visibility owners. */
export class DevLayers {
  readonly element = Object.assign(document.createElement('section'), { id: 'dev-layers' });
  private tree = document.createElement('div');
  private status = document.createElement('div');
  private panel = document.createElement('div');
  private button = document.createElement('button');
  private paths = new Set<string>();
  private choices: Record<string, boolean> = {};
  private masked = new Map<THREE.Object3D, number>();
  private shadows = new Map<THREE.Mesh, boolean>();
  private materials = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  private plain = new WeakMap<THREE.Material, THREE.Material>();
  private plainOwned = new Set<THREE.Material>();
  private filtered = new Map<THREE.Mesh, Filtered>();
  private dirty = true;
  private open = false;
  private enabled = devToolsEnabled(location);
  private select = document.createElement('select');
  private viewDirection = new THREE.Vector3();

  constructor(private scene: THREE.Scene, private coarse: CoarseTiles | null, private renderer?: THREE.WebGPURenderer,
    private setViewDistance?: (radius: number) => void, private camera?: THREE.PerspectiveCamera) {
    if (!this.enabled) return;
    let pass: 'auto' | 'first' | 'full' = 'auto';
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}');
      this.choices = saved.choices ?? {};
      if (['auto', 'first', 'full'].includes(saved.pass)) pass = saved.pass;
    } catch { /* Storage unavailable: session defaults. */ }
    this.button.type = 'button'; this.button.textContent = 'Layers · F4 / LB';
    this.button.onclick = () => this.toggle();
    this.button.setAttribute('aria-expanded', 'false');
    this.panel.hidden = true;
    const heading = document.createElement('label'); heading.textContent = 'Visual pass ';
    for (const [value, text] of [['auto', 'Auto'], ['first', 'First pass only'], ['full', 'Full pass (textured)']]) {
      this.select.append(Object.assign(document.createElement('option'), { value, textContent: text }));
    }
    this.select.value = pass;
    this.select.onchange = () => { this.coarse?.setVisualPass(this.select.value as typeof pass); this.save(); };
    this.coarse?.setVisualPass(pass);
    heading.append(this.select);
    this.status.setAttribute('role', 'status');
    this.panel.append(heading);
    if (coarse && setViewDistance) {
      const distance = document.createElement('select'); distance.id = 'dev-view-distance';
      const row = document.createElement('label'); row.textContent = 'View distance '; row.style.display = 'block';
      for (const radius of [0, 1000, 2000, 2500, 3000, 4000, 5000, 6000, 8000]) distance.append(Object.assign(document.createElement('option'), {
        value: String(radius), textContent: radius ? `${radius / 1000} km` : 'Short range',
      }));
      distance.value = String(coarse.screenshotScenery.radius);
      distance.onchange = () => {
        const radius = Number(distance.value);
        coarse.screenshotScenery.setRadius(radius, this.sceneryView());
        setViewDistance(radius);
      };
      row.append(distance); this.panel.append(row);
      const hint = document.createElement('div');
      hint.textContent = 'Above 1 km: lightweight scenery follows the camera as you turn and move.';
      this.panel.append(hint);
    }
    this.panel.append(this.status, this.tree);
    this.element.append(this.button, this.panel); document.body.append(this.element);
  }

  private sceneryView() {
    if (!this.camera) return undefined;
    const direction = this.camera.getWorldDirection(this.viewDirection);
    const length = Math.hypot(direction.x, direction.z);
    if (length < .01) return undefined;
    return { x: this.camera.position.x, y: this.camera.position.y, z: this.camera.position.z,
      dx: direction.x / length, dz: direction.z / length,
      halfAngle: Math.atan(Math.tan(this.camera.fov * Math.PI / 360) * this.camera.aspect) + .15 };
  }

  private save() {
    try { localStorage.setItem(KEY, JSON.stringify({ choices: this.choices, pass: this.select.value })); } catch { /* Session choices remain. */ }
  }
  private allowed(path: string) {
    const [group] = path.split('/');
    return (this.choices[path] ?? this.choices[group] ?? true);
  }
  private discover(path: string | null) {
    if (path && !this.paths.has(path)) { this.paths.add(path); this.dirty = true; }
    return path;
  }
  get nameTagsVisible() { return this.allowed('People/Name Tags'); }
  toggle() {
    this.open = !this.open; this.panel.hidden = !this.open;
    this.button.setAttribute('aria-expanded', String(this.open));
    if (this.open) void document.exitPointerLock?.();
  }
  handle(input: Input, playing: boolean) {
    if (!this.enabled) return;
    this.element.hidden = !playing;
    if (playing && input.consume('layerPanel')) this.toggle();
  }
  /** Restore camera masks before simulation/streaming, which own ordinary visibility and layers. */
  restore() {
    for (const [object, mask] of this.masked) object.layers.mask = mask;
    this.masked.clear();
    for (const [mesh, cast] of this.shadows) mesh.castShadow = cast;
    this.shadows.clear();
    for (const [mesh, material] of this.materials) mesh.material = material;
    this.materials.clear();
    for (const owned of this.filtered.values()) owned.ranges.restore();
  }
  private hide(object: THREE.Object3D) {
    this.masked.set(object, object.layers.mask); object.layers.mask = 0;
  }
  update() {
    if (!this.enabled) return;
    this.discover('People/Name Tags');
    const seen = new Set<THREE.Mesh>();
    const filtering = [...this.paths].some(p => !this.allowed(p));
    const shadows = this.allowed('Effects/Shadows');
    this.scene.traverse(object => {
      const mesh = object as THREE.Mesh;
      // Inherit semantic roots for client objects and authored glTF nodes.
      let path: string | null = null;
      for (let parent: THREE.Object3D | null = object; parent && !path; parent = parent.parent)
        path = layerPath(parent.userData, parent.name);
      this.discover(path);
      if (mesh.isMesh && path?.startsWith('Ground/')) {
        this.discover('Ground/Surface Textures');
        if (!this.allowed('Ground/Surface Textures')) {
          const plain = (source: THREE.Material) => {
            let material = this.plain.get(source);
            if (!material) {
              const pbr = source as THREE.MeshStandardMaterial;
              material = new THREE.MeshStandardMaterial({ color: pbr.color ?? 0x888888, roughness: .9, side: source.side });
              this.plain.set(source, material); this.plainOwned.add(material);
              const owned = material;
              source.addEventListener('dispose', () => { owned.dispose(); this.plainOwned.delete(owned); });
            }
            return material;
          };
          this.materials.set(mesh, mesh.material);
          mesh.material = Array.isArray(mesh.material) ? mesh.material.map(plain) : plain(mesh.material);
        }
      }
      if (mesh.isMesh && !object.userData.shadowBatch && (!shadows || filtering)) {
        this.shadows.set(mesh, mesh.castShadow);
        mesh.castShadow = shadows && !path?.startsWith('Ground/');
      }
      if (mesh.isMesh && Array.isArray(mesh.userData.objects)) {
        seen.add(mesh);
        const rows = mesh.userData.objects as (Data & { start: number; count: number })[];
        const paths = rows.map(row => this.discover(layerPath(row, mesh.name)) ?? path);
        if (!this.filtered.has(mesh) && paths.every(p => p === paths[0])) {
          if (paths[0] && !this.allowed(paths[0])) this.hide(mesh);
          return;
        }
        let owned = this.filtered.get(mesh);
        if (owned && !owned.ranges.current) {
          owned.release(); owned = undefined;
        }
        if (!owned) {
          const ranges = new DevLayerRanges(mesh, rows, paths);
          const disposed = () => {
            mesh.removeEventListener('dispose' as never, disposed);
            const material = this.materials.get(mesh);
            if (material) { mesh.material = material; this.materials.delete(mesh); }
            ranges.restore();
            if (this.renderer) retireAfterFrame(this.renderer, () => ranges.dispose()); else ranges.dispose();
            this.filtered.delete(mesh);
          };
          mesh.addEventListener('dispose' as never, disposed);
          owned = { ranges, release: disposed }; this.filtered.set(mesh, owned);
        }
        if (!this.materials.has(mesh)) this.materials.set(mesh, mesh.material);
        owned.ranges.apply(p => this.allowed(p));
      } else if (path && !this.allowed(path)) this.hide(object);
      // Aggregate sun shadows cannot retain removed geometry. Use original casters while filtering.
      if (object.userData.shadowBatch && filtering) this.hide(object);
    });
    for (const [mesh, owned] of this.filtered) if (!seen.has(mesh)) {
      owned.release();
    }
    const scenery = this.coarse?.screenshotScenery;
    if (scenery && scenery.radius > 1000) {
      const view = this.sceneryView();
      if (view) scenery.setView(view, performance.now());
    }
    const progress = scenery?.progress;
    this.status.textContent = [this.coarse?.comparisonStatus,
      scenery?.heading !== null && scenery?.heading !== undefined ? `View direction: ${Math.round(scenery.heading)}°` : '',
      progress?.cataloguePending ? 'Reading existing scenery…' : '',
      progress?.missing ? `${progress.missing} source tiles unavailable` : '', 
      scenery?.radius && progress ? `Scenery ${progress.loaded}/${progress.total} · ${progress.pending} awaiting full detail${progress.errors ? ` · ${progress.errors} retrying` : ''}` : '',
    ].filter(Boolean).join(' · ');
    if (this.dirty) this.renderTree();
    const readout = document.getElementById('position-hud');
    const bottom = readout && !readout.hidden ? readout.getBoundingClientRect().bottom + 4 : 86;
    this.element.style.top = `${bottom}px`;
  }
  private renderTree() {
    this.dirty = false;
    const expanded = new Set([...this.tree.querySelectorAll<HTMLDetailsElement>('details[open]')].map(d => d.dataset.group));
    this.tree.replaceChildren();
    const groups = [...new Set([...this.paths].map(p => p.split('/')[0]))].sort();
    for (const group of groups) {
      const children = [...this.paths].filter(p => p.startsWith(`${group}/`)).sort();
      const details = document.createElement('details'); details.dataset.group = group; details.open = expanded.has(group);
      const summary = document.createElement('summary');
      const row = (text: string, paths: string[]) => {
        const wrapper = document.createElement('label'), box = document.createElement('input'); box.type = 'checkbox';
        box.checked = paths.every(p => this.allowed(p)); box.indeterminate = !box.checked && paths.some(p => this.allowed(p));
        box.onchange = () => {
          if (paths.length > 1 || text === group) this.choices[group] = box.checked;
          for (const p of paths) this.choices[p] = box.checked;
          this.save(); this.dirty = true;
        };
        wrapper.append(box, document.createTextNode(text)); return wrapper;
      };
      summary.append(row(group, children)); details.append(summary);
      for (const child of children) details.append(row(child.split('/')[1], [child]));
      this.tree.append(details);
    }
  }
  dispose() {
    this.coarse?.screenshotScenery.setRadius(0); this.setViewDistance?.(0);
    this.restore();
    for (const owned of this.filtered.values()) owned.release();
    this.filtered.clear();
    for (const material of this.plainOwned) material.dispose();
    this.plainOwned.clear(); this.element.remove();
  }
}
