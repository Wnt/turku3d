// Real-tile headless acceptance: run with `npm --prefix game exec vitest run -c vitest.pole.config.ts`.
import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import * as THREE from 'three/webgpu';
import fs from 'node:fs';
import path from 'node:path';
import { PhysicsWorld } from '../src/physics/world';
import { PoleSession } from '../src/gameplay/poles/session';
import { applyPoleFine } from '../src/gameplay/poles/fine';
import { extractPoles } from '../src/world/poleExtraction';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import type { PoleState } from '../src/physics/protocol';

const data = process.env.TURKU3D_DATA!;
const work = process.env.TURKU3D_WORK!;
const source = JSON.parse(fs.readFileSync(path.join(work, 'reports/pole-source.json'), 'utf8')) as Record<string, { id: string; x: number; z: number }[]>;
const idle = { vx: 0, vz: 0, jump: false };
const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };

type Element = { children: Element[]; classList: { add: (...v: string[]) => void; remove: (...v: string[]) => void; toggle: (v: string, b: boolean) => void; contains: (v: string) => boolean }; textContent: string; offsetWidth: number; append: (...e: Element[]) => void; remove: () => void; setAttribute: (k: string, v: string) => void };
function element(): Element {
  const classes = new Set<string>();
  return { children: [], textContent: '', offsetWidth: 1,
    classList: { add: (...v) => v.forEach(s => classes.add(s)), remove: (...v) => v.forEach(s => classes.delete(s)),
      toggle: (v, b) => { if (b) classes.add(v); else classes.delete(v); }, contains: v => classes.has(v) },
    append(...children) { this.children.push(...children); }, remove() {}, setAttribute() {},
  };
}

async function run(kind: 'full' | 'draft') {
  const root = kind === 'full' ? path.join(data, 'live/tiles') : path.join(work, 'build/coarse/t0_2_0');
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8')) as { collision: Record<string, { url: string }> };
  const pw = new PhysicsWorld(R);
  const cell = kind === 'full' ? /^c_[0-3]_[0-3]$/ : /^c_(8|9|10|11)_[0-3]$/;
  for (const [id, entry] of Object.entries(manifest.collision)) {
    if (!cell.test(id)) continue;
    const bytes = fs.readFileSync(path.join(root, entry.url));
    await pw.loadTile(id, bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  }
  pw.world.step();
  const tries: string[] = [];
  for (const p of source[kind]) {
    const ground = pw.raycast({ x: p.x, y: 50, z: p.z }, { x: 0, y: -1, z: 0 }, 100);
    const rear = pw.raycast({ x: p.x - 5, y: 50, z: p.z }, { x: 0, y: -1, z: 0 }, 100);
    if (!ground || !rear || Math.abs(ground.point.y - rear.point.y) > 0.5) continue;
    const base = { x: p.x, y: ground.point.y, z: p.z };
    pw.setPoles([{ id: p.id, base, yaw: 0, height: 2.7 }], []);
    pw.spawnVehicle({ x: p.x - 5, y: rear.point.y + 0.75, z: p.z }, 0);
    for (let i = 0; i < 25; i++) pw.fixedStep(idle, coast);
    pw.vehicle!.body.setLinvel({ x: 7, y: 0, z: 0 }, true);
    let breakState: PoleState | undefined;
    let minSpeed = 7;
    for (let i = 0; i < 110; i++) {
      pw.fixedStep(idle, coast);
      minSpeed = Math.min(minSpeed, pw.vehicle!.body.linvel().x);
      const state = pw.poleStates().find(s => s.id === p.id)!;
      if (state.brokeNow) { breakState = state; break; }
    }
    if (breakState) {
      const dom = { createElement: () => element() };
      Object.assign(globalThis, { document: dom });
      const parent = element();
      const model = new THREE.Group();
      const node = new THREE.Group(); node.position.set(base.x, base.y, base.z);
      node.userData = { prop_type: 'sign', pole_id: p.id, pole_height: 2.7 };
      model.add(node);
      const tiles = { modelEvents: new THREE.EventDispatcher(), models: new Set([model]) };
      let wallet = 100, fines = 0, voiceCalls = 0, synced = 0;
      const missions = { finePole() { fines++; wallet = applyPoleFine(wallet); } };
      const voice = { show() { voiceCalls++; }, snapshot: () => ({ playing: null, pending: null }) };
      const session = new PoleSession(tiles as never, { setPoles() {}, breakPole() {} } as never,
        missions as never, voice as never, parent as never);
      const client = { addEventListener() {}, breakPole() { synced++; }, state: { brokenPoles: [] } };
      session.setClient(client as never);
      session.update([breakState, breakState], base, 200);
      expect(fines).toBe(1); expect(wallet).toBe(50); expect(voiceCalls).toBe(1); expect(synced).toBe(1);
      expect(parent.children[0].classList.contains('visible')).toBe(true);
      expect(parent.children[0].children[1].textContent.length).toBeGreaterThan(0);
      session.dispose();
      const result = { kind, id: p.id, base, minCarSpeed: minSpeed, collision: minSpeed < 7,
        broken: breakState.broken, fine: 50, notice: true, voice: voiceCalls, multiplayer: synced, tried: tries.length + 1 };
      fs.mkdirSync(path.join(work, 'reports'), { recursive: true });
      fs.writeFileSync(path.join(work, `reports/pole-qa-${kind}.json`), JSON.stringify(result, null, 2));
      console.log(result);
      pw.world.free();
      return;
    }
    tries.push(p.id);
    pw.setPoles([], [p.id]);
    pw.resetVehicle({ x: p.x - 5, y: rear.point.y + 0.75, z: p.z }, 0);
  }
  pw.world.free();
  throw new Error(`${kind}: no successful pole impact among ${tries.length} candidates`);
}

test('published t0_0_0: contact, break, fine, notice and multiplayer', async () => run('full'));
test('draft t0_2_0: contact, break, fine, notice and multiplayer', async () => run('draft'));


test('published and rebuilt draft GLBs retain every runtime pole with source positions', async () => {
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
    .registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
  for (const kind of ['full', 'draft'] as const) {
    const root = kind === 'full' ? path.join(data, 'live/tiles') : path.join(work, 'build/coarse/t0_2_0');
    const tile = kind === 'full' ? 't0_0_0' : 't0_2_0';
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8')) as { visual: Record<string, { url: string }> };
    const doc = await io.read(path.join(root, manifest.visual[tile].url));
    const model = new THREE.Group();
    for (const node of doc.getRoot().listNodes()) {
      const extras = node.getExtras();
      if (!extras.pole_id && !Array.isArray(extras.objects)) continue;
      const primitive = node.getMesh()?.listPrimitives()[0];
      if (!primitive) continue;
      const indices = primitive.getIndices()?.getArray(), positions = primitive.getAttribute('POSITION')?.getArray();
      if (!indices || !positions) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setIndex(Array.from(indices));
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
      mesh.userData = extras;
      const matrix = node.getWorldMatrix();
      mesh.position.set(matrix[12], matrix[13], matrix[14]);
      model.add(mesh);
    }
    const extracted = extractPoles(model);
    expect(extracted).toHaveLength(source[kind].length);
    const original = new Map(source[kind].map(p => [p.id, p]));
    for (const { spec } of extracted) {
      const reference = original.get(spec.id);
      expect(reference).toBeDefined();
      expect(Math.hypot(spec.base.x - reference!.x, spec.base.z - reference!.z)).toBeLessThan(0.04);
    }
    fs.writeFileSync(path.join(work, `reports/pole-placement-${kind}.json`),
      JSON.stringify(extracted.map(({ spec }) => spec), null, 2));
    console.log(`${kind} ${tile}: ${extracted.length} runtime pole candidates`);
  }
});
