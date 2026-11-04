// The in-world portal arches (gameplay/portals.ts): two glowing posts and a beam across the lanes, a light curtain that
// fades upwards and a floating city label, coloured per target city; a city whose tiles are not served yet shows grey
// with "Coming soon". Same additive glow materials as the mission zones (zones.ts), so the bloom pass picks them up.
import * as THREE from 'three/webgpu';
import { float, uniform, uv } from 'three/tsl';
import { headingToYaw } from '../core/origin';
import type { CityName } from '../core/city';
import { PORTAL_HEIGHT, portalCity, type PortalDef } from './portals';

export const PORTAL_COLOURS: Record<CityName, string> = {
  turku: '#4fd2ff', espoo: '#36f0c8', amsterdam: '#ff9a3c', rotterdam: '#7dff6a',
};
const SOON_COLOUR = '#9aa3ad';
const GLOW = 2.6;
const POST = 0.45;
/** Labels show up to this distance; the arch glows at full strength up to FADE_START and is gone at FADE_END. */
const LABEL_RANGE = 140;
const FADE_START = 160;
const FADE_END = 260;
const PULSE_SECONDS = 2.2;
const PROBE_RETRY_SECONDS = 1;

/** Ground under a portal from a downward ray starting at `top` (null: from the sky), or null while not resident. */
type GroundProbe = (x: number, z: number, top: number | null) => Promise<number | null>;

interface Arch {
  def: PortalDef;
  group: THREE.Group;
  strength: THREE.UniformNode<'float', number>;
  materials: THREE.MeshBasicNodeMaterial[];
  label: HTMLDivElement;
  sub: HTMLSpanElement;
  y: number | null;
  probing: boolean;
  nextProbe: number;
  soon: boolean | null;
  labelShown: boolean;
}

export class PortalMarkers {
  readonly group = new THREE.Group();
  readonly labels = document.createElement('div');
  private readonly notice = document.createElement('div');
  private noticeUntil = 0;
  private readonly arches: Arch[] = [];
  private time = 0;
  private readonly projected = new THREE.Vector3();

  constructor(scene: THREE.Object3D, portals: readonly PortalDef[], private readonly ground: GroundProbe, parent: HTMLElement) {
    this.group.name = 'city-portals';
    scene.add(this.group);
    this.labels.id = 'portal-labels';
    this.notice.id = 'portal-notice';
    this.notice.setAttribute('role', 'status');
    this.notice.hidden = true;
    parent.append(this.labels, this.notice);
    for (const def of portals) this.add(def);
  }

  /** The ground height of portal `id`, or null until it is known. */
  groundOf(id: string): number | null { return this.arches.find(a => a.def.id === id)?.y ?? null; }

  /** Grey "Coming soon" (true), open (false) or not known yet (null). */
  setSoon(id: string, soon: boolean | null) {
    const arch = this.arches.find(a => a.def.id === id);
    if (!arch || arch.soon === soon) return;
    arch.soon = soon;
    const colour = new THREE.Color(soon ? SOON_COLOUR : PORTAL_COLOURS[arch.def.to]).multiplyScalar(GLOW);
    for (const m of arch.materials) m.color.copy(colour);
    arch.label.style.setProperty('--zone-colour', soon ? SOON_COLOUR : PORTAL_COLOURS[arch.def.to]);
    arch.sub.textContent = soon ? 'Coming soon' : arch.def.to === 'turku' ? 'Return portal' : 'Portal';
  }

  /** A short line under the HUD (a closed portal, a failed trip). */
  say(text: string, now: number, seconds = 4) {
    this.notice.textContent = text;
    this.notice.hidden = false;
    this.noticeUntil = now + seconds * 1000;
  }

  update(dt: number, camera: THREE.Camera, viewer: { x: number; z: number }, now: number) {
    this.time += dt;
    if (!this.notice.hidden && now > this.noticeUntil) this.notice.hidden = true;
    const width = innerWidth, height = innerHeight;
    for (const arch of this.arches) {
      if (arch.y === null) { this.probe(arch); continue; }
      const metres = Math.hypot(arch.def.x - viewer.x, arch.def.z - viewer.z);
      const fade = Math.min(1, Math.max(0, (FADE_END - metres) / (FADE_END - FADE_START)));
      arch.strength.value = fade * (0.8 + 0.2 * Math.sin((2 * Math.PI * this.time) / PULSE_SECONDS)) * (arch.soon ? 0.55 : 1);
      arch.group.visible = fade > 0;
      const p = this.projected.set(arch.def.x, arch.y + PORTAL_HEIGHT + 0.6, arch.def.z).project(camera);
      arch.labelShown = metres <= LABEL_RANGE && p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1;
      arch.label.style.display = arch.labelShown ? '' : 'none';
      if (arch.labelShown) {
        arch.label.style.transform = `translate(${((p.x + 1) / 2) * width}px, ${((1 - p.y) / 2) * height}px) translate(-50%, -100%)`;
        arch.label.style.opacity = String(Math.min(1, (LABEL_RANGE - metres) / 15 + 0.35));
      }
    }
  }

  /** Map markers: every portal with its target city's name and colour. */
  markers(): { x: number; z: number; label: string; colour: string; soon: boolean }[] {
    return this.arches.map(a => ({ x: a.def.x, z: a.def.z, label: portalCity(a.def).title,
      colour: a.soon ? SOON_COLOUR : PORTAL_COLOURS[a.def.to], soon: a.soon === true }));
  }

  snapshot() {
    return this.arches.map(a => ({ id: a.def.id, to: a.def.to, x: a.def.x, z: a.def.z, y: a.y, soon: a.soon,
      visible: a.group.visible, labelShown: a.labelShown, label: a.label.textContent }));
  }

  dispose() {
    this.group.removeFromParent();
    for (const arch of this.arches) for (const m of arch.materials) m.dispose();
    this.labels.remove();
    this.notice.remove();
  }

  private add(def: PortalDef) {
    const strength = uniform(0);
    const colour = new THREE.Color(PORTAL_COLOURS[def.to]).multiplyScalar(GLOW);
    const glow = (opacity: THREE.Node) => {
      const m = new THREE.MeshBasicNodeMaterial({ color: colour, transparent: true, depthWrite: false,
        blending: THREE.AdditiveBlending, side: THREE.DoubleSide, fog: false });
      m.opacityNode = opacity;
      return m;
    };
    const solid = glow(strength.mul(0.95)), curtain = glow(strength.mul(float(1).sub(uv().y).pow(1.6)).mul(0.42));
    const floor = glow(strength.mul(0.3));
    const w = def.width, h = PORTAL_HEIGHT;
    const group = new THREE.Group();
    group.name = `portal:${def.id}`;
    group.visible = false;
    const post = new THREE.BoxGeometry(POST, h, POST).translate(0, h / 2, 0);
    const left = new THREE.Mesh(post, solid), right = new THREE.Mesh(post, solid);
    left.position.x = -w / 2; right.position.x = w / 2;
    const beam = new THREE.Mesh(new THREE.BoxGeometry(w + POST, POST, POST).translate(0, h, 0), solid);
    const sheet = new THREE.Mesh(new THREE.PlaneGeometry(w, h).translate(0, h / 2, 0), curtain);
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(w, Math.min(def.depth, 2)).rotateX(-Math.PI / 2).translate(0, 0.04, 0), floor);
    group.add(left, right, beam, sheet, strip);
    for (const mesh of group.children) mesh.renderOrder = 10;
    // Local +X runs across the lanes: the arch faces the travel direction.
    group.rotation.y = headingToYaw(def.headingDeg);
    group.position.set(def.x, 0, def.z);
    this.group.add(group);
    const label = document.createElement('div');
    label.className = 'zone-label portal-label';
    const name = document.createElement('strong');
    name.textContent = portalCity(def).title;
    const sub = document.createElement('span');
    sub.textContent = def.to === 'turku' ? 'Return portal' : 'Portal';
    label.append(name, sub);
    label.style.setProperty('--zone-colour', PORTAL_COLOURS[def.to]);
    label.style.display = 'none';
    this.labels.append(label);
    this.arches.push({ def, group, strength, materials: [solid, curtain, floor], label, sub, y: null, probing: false,
      nextProbe: 0, soon: null, labelShown: false });
  }

  private probe(arch: Arch) {
    if (arch.probing || this.time < arch.nextProbe) return;
    arch.probing = true;
    void this.ground(arch.def.x, arch.def.z, arch.def.rayTop).then(y => {
      arch.probing = false;
      if (y === null) { arch.nextProbe = this.time + PROBE_RETRY_SECONDS; return; }
      arch.y = y;
      arch.group.position.y = y;
    }, () => { arch.probing = false; arch.nextProbe = this.time + PROBE_RETRY_SECONDS; });
  }
}
