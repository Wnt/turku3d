// Birds in the sky: where every bird is at a shared-clock second. Pure and deterministic, so every player sees the
// same gull over the same quay: groups are hashed from grid cells of the city, and a bird's place is a closed-form
// function of time (no simulation state, no history, nothing to synchronise). render/birds.ts draws the result.
import { CITY } from '../../core/city';
import { gullPresence } from '../../audio/gulls';

/** How a species moves.
 *  soar: each bird circles alone on a slow wobbly loop, gliding most of the time (gulls, the eagle);
 *  flock: the birds share one loop and trail along it as a loose cloud (jackdaws, pigeons);
 *  dart: each bird on its own tight, fast loop with flap bursts (swifts);
 *  travel: a skein or a pair crosses the sky on a straight line (geese in a V, crows commuting). */
export type Behaviour = 'soar' | 'flock' | 'dart' | 'travel';

export interface Species {
  name: string;
  behaviour: Behaviour;
  /** Wingspan (m), and wing chord relative to the shared mesh (long narrow gull wings < 1 < broad crow wings). */
  span: number; chord: number;
  /** Albedo of the body, the wings and the wing tips (grey levels: these birds are black, grey and white). */
  body: number; wing: number; tip: number;
  /** Air speed (m/s), wing beats per second, beat amplitude (rad), and the share of time spent gliding. */
  speed: number; flapHz: number; flapAngle: number; glide: number;
  /** Grid cell (m) and the chance that a cell holds a group; birds per group. */
  cell: number; chance: number; count: readonly [number, number];
  /** Height above the ground (m), and the loop radius (m; travel: unused). */
  height: readonly [number, number]; radius: readonly [number, number];
  /** Drawn up to this distance (m): a bird is a pixel or two there. */
  range: number;
  /** How much rain grounds the species (0: flies in any weather), and how much dusk gathers it (jackdaws roost). */
  rainShy: number; dusk: number;
}

/** The birds of a Finnish coastal city. Sizes, wing-beat rates and speeds are the species' own. */
export const SPECIES: readonly Species[] = [
  { name: 'herring gull', behaviour: 'soar', span: 1.4, chord: 0.8, body: 0.85, wing: 0.55, tip: 0.05, speed: 10, flapHz: 2.8, flapAngle: 0.55,
    glide: 0.7, cell: 192, chance: 0.05, count: [1, 3], height: [14, 70], radius: [18, 60], range: 650, rainShy: 0.3, dusk: 0 },
  { name: 'jackdaw', behaviour: 'flock', span: 0.7, chord: 1.15, body: 0.045, wing: 0.035, tip: 0.03, speed: 12, flapHz: 4.6, flapAngle: 0.75,
    glide: 0.12, cell: 384, chance: 0.22, count: [8, 34], height: [28, 75], radius: [70, 170], range: 520, rainShy: 0.6, dusk: 1 },
  { name: 'feral pigeon', behaviour: 'flock', span: 0.68, chord: 1.1, body: 0.3, wing: 0.36, tip: 0.12, speed: 15, flapHz: 6.5, flapAngle: 0.85,
    glide: 0.1, cell: 320, chance: 0.25, count: [6, 18], height: [22, 45], radius: [40, 90], range: 420, rainShy: 0.7, dusk: 0 },
  { name: 'common swift', behaviour: 'dart', span: 0.44, chord: 0.6, body: 0.04, wing: 0.04, tip: 0.04, speed: 17, flapHz: 8, flapAngle: 0.6,
    glide: 0.45, cell: 448, chance: 0.3, count: [3, 8], height: [24, 80], radius: [22, 60], range: 320, rainShy: 0.9, dusk: 0.3 },
  { name: 'barnacle goose', behaviour: 'travel', span: 1.35, chord: 1, body: 0.4, wing: 0.22, tip: 0.05, speed: 17, flapHz: 3.8, flapAngle: 0.5,
    glide: 0, cell: 1024, chance: 0.14, count: [7, 23], height: [80, 150], radius: [0, 0], range: 900, rainShy: 0.4, dusk: 0.2 },
  { name: 'hooded crow', behaviour: 'travel', span: 0.98, chord: 1.25, body: 0.4, wing: 0.04, tip: 0.035, speed: 11, flapHz: 3.6, flapAngle: 0.7,
    glide: 0.05, cell: 512, chance: 0.45, count: [1, 2], height: [20, 48], radius: [0, 0], range: 450, rainShy: 0.4, dusk: 0 },
  { name: 'white-tailed eagle', behaviour: 'soar', span: 2.2, chord: 1.4, body: 0.16, wing: 0.11, tip: 0.07, speed: 12, flapHz: 2, flapAngle: 0.4,
    glide: 0.95, cell: 2048, chance: 0.3, count: [1, 1], height: [140, 260], radius: [90, 180], range: 1500, rainShy: 0.8, dusk: 0 },
];

/** A travelling group repeats along its line every this many seconds; a group is only drawn while the camera is so
 *  near its anchor that the wrap point is out of sight. */
const TRAVEL_PERIOD = 300;
/** A group's weight falls to nothing over the last metres of its anchor's reach, so nothing pops as the camera moves. */
const ANCHOR_FADE = 250;
/** The floats sampleBirds writes per bird. */
export const BIRD_STRIDE = 14;

interface Path {
  /** Loop: centre, radius, angular rate (rad/s, signed), phase, two harmonics, height and its swing. */
  loop?: { x: number; z: number; r: number; w: number; p: number; a2: number; p2: number; a3: number; p3: number; h: number; dh: number; ph: number };
  /** Line through the anchor: direction, speed, length, offset and height. */
  line?: { x: number; z: number; dx: number; dz: number; v: number; length: number; offset: number; h: number };
}
interface Member {
  path: Path;
  /** Seconds behind the path's head, and a fixed offset from it (m). */
  lag: number; ox: number; oy: number; oz: number;
  /** Shown while the species' activity exceeds this (0..1): a flock thins out bird by bird towards night. */
  threshold: number;
  /** Phases of the wing beat, the glide rhythm and the small wander. */
  beat: number; rhythm: number; wander: number; size: number;
}
export interface Group { species: number; x: number; z: number; reach: number; members: Member[] }

/** Uniform [0, 1) from a cell, a species and a draw index. */
export function cellHash(ix: number, iz: number, salt: number) {
  let h = Math.imul(ix, 0x27d4eb2d) ^ Math.imul(iz, 0x165667b1) ^ Math.imul(salt + 1, 0x9e3779b1);
  h = Math.imul(h ^ h >>> 15, 0x85ebca6b); h = Math.imul(h ^ h >>> 13, 0xc2b2ae35);
  return ((h ^ h >>> 16) >>> 0) / 4294967296;
}
function stream(seed: number) {
  let a = seed >>> 0;
  return () => { a = a + 0x6D2B79F5 >>> 0; let t = a; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
const smooth = (a: number, b: number, v: number) => { const t = clamp01((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const TAU = 2 * Math.PI;

/** The group of one species in one cell, or null: most cells are empty. Gulls gather at the water (the same river
 *  and harbour their calls come from, audio/gulls.ts); a few wander inland. */
export function groupAt(speciesIndex: number, ix: number, iz: number, city: string = CITY.name): Group | null {
  const s = SPECIES[speciesIndex], salt = speciesIndex * 16;
  const x = (ix + cellHash(ix, iz, salt + 1)) * s.cell, z = (iz + cellHash(ix, iz, salt + 2)) * s.cell;
  const water = s.name === 'herring gull' ? gullPresence({ x, y: 0, z }, city).presence : 0;
  if (cellHash(ix, iz, salt) >= s.chance + water * 0.45) return null;
  const random = stream(Math.floor(cellHash(ix, iz, salt + 3) * 4294967296));
  const between = (range: readonly [number, number]) => range[0] + (range[1] - range[0]) * random();
  const count = Math.round(between(s.count)) + (water > 0.5 ? 1 : 0);
  const loop = (cx: number, cz: number, wobble: number): Path => {
    const r = between(s.radius);
    return { loop: { x: cx, z: cz, r, w: (random() < 0.5 ? -1 : 1) * s.speed / r, p: random() * TAU, a2: wobble * (0.5 + 0.5 * random()), p2: random() * TAU,
      a3: wobble * 0.6 * random(), p3: random() * TAU, h: between(s.height), dh: (s.height[1] - s.height[0]) * 0.12 * random(), ph: random() * TAU } };
  };
  const members: Member[] = [];
  const member = (path: Path, lag: number, ox: number, oy: number, oz: number) => members.push({ path, lag, ox, oy, oz,
    threshold: members.length === 0 ? 0.02 : 0.02 + 0.9 * random(), beat: random() * TAU, rhythm: random() * TAU, wander: random() * TAU, size: 0.92 + 0.16 * random() });
  let reach = 0;
  if (s.behaviour === 'travel') {
    const heading = random() * TAU, length = s.speed * TRAVEL_PERIOD;
    const path: Path = { line: { x, z, dx: Math.cos(heading), dz: Math.sin(heading), v: s.speed, length, offset: random() * length, h: between(s.height) } };
    // A V: the leader at the point, the others alternately on each arm, each a little behind and beside the last.
    const gap = s.span * 1.25, skein = count > 2;
    for (let i = 0; i < count; i++) {
      const rank = Math.ceil(i / 2), side = i % 2 ? 1 : -1;
      if (skein) member(path, rank * gap * 1.1 / s.speed, side * rank * gap * 0.75, (random() - 0.5) * 0.8, 0);
      else member(path, i * (1.5 + 2 * random()), i * (2 + 3 * random()), i * (random() - 0.5) * 3, 0);
    }
    reach = length / 2;
  } else if (s.behaviour === 'flock') {
    const path = loop(x, z, 0.22), spread = 2.2 * Math.sqrt(count);
    for (let i = 0; i < count; i++) member(path, random() * spread / s.speed * 2.2, (random() - 0.5) * spread * 1.6, (random() - 0.5) * spread * 0.7, (random() - 0.5) * spread * 1.6);
    reach = path.loop!.r * 1.5 + spread;
  } else {
    const scatter = s.behaviour === 'dart' ? 70 : 90;
    for (let i = 0; i < count; i++) {
      const path = loop(x + (random() - 0.5) * scatter, z + (random() - 0.5) * scatter, s.behaviour === 'dart' ? 0.45 : 0.18);
      member(path, 0, 0, 0, 0);
      reach = Math.max(reach, scatter / 2 + path.loop!.r * 1.7);
    }
  }
  return { species: speciesIndex, x, z, reach, members };
}

const point = { x: 0, y: 0, z: 0 };
/** A member's place at second `t`, height above the ground (into `point`). */
function place(m: Member, t: number) {
  const time = t - m.lag, loop = m.path.loop, line = m.path.line;
  if (loop) {
    const a = loop.w * time + loop.p, r = loop.r * (1 + loop.a2 * Math.sin(2 * a + loop.p2) + loop.a3 * Math.sin(3 * a + loop.p3));
    point.x = loop.x + r * Math.cos(a) + m.ox; point.z = loop.z + r * Math.sin(a) + m.oz;
    point.y = loop.h + loop.dh * Math.sin(a + loop.ph) + m.oy;
  } else if (line) {
    const along = line.v * t + line.offset, s = along - Math.floor(along / line.length) * line.length - line.length / 2 - m.lag * line.v;
    point.x = line.x + line.dx * s - line.dz * m.ox; point.z = line.z + line.dz * s + line.dx * m.ox;
    point.y = line.h + m.oy;
  }
  // Every bird wanders a little around its place, so a flock is never a rigid shape.
  point.x += 0.6 * Math.sin(t * 0.83 + m.wander); point.y += 0.45 * Math.sin(t * 1.31 + m.wander * 2); point.z += 0.6 * Math.cos(t * 0.71 + m.wander * 3);
  return point;
}

export interface BirdConditions {
  /** 0 at night, 1 by day; 1 around sunrise and sunset, else 0; rain 0..1. */
  daylight: number; dusk: number; rain: number;
}
/** Day, dusk and rain from the sun's elevation (deg) and the weather. Birds fly through the low sun of morning and
 *  evening and roost once it is 6 deg down. */
export function birdConditions(sunElevation: number, rain: number): BirdConditions {
  return { daylight: smooth(-6, -1, sunElevation), dusk: smooth(-5, -1, sunElevation) * (1 - smooth(5, 12, sunElevation)), rain: clamp01(rain) };
}
/** How much of a species is in the air (0..1): members with a lower threshold fly. */
export function activity(s: Species, c: BirdConditions) {
  return clamp01(c.daylight * (1 - s.rainShy * c.rain) * (1 - 0.45 * s.dusk * (1 - c.dusk)));
}

export interface BirdView { x: number; y: number; z: number; /** Ground height under the viewer (m). */ ground: number }

/** The groups that can be in sight of `view`. Cells are hashed once and kept (`cache`: call often, it is cheap). */
export function groupsNear(view: BirdView, cache: Map<number, Group | null>, city: string = CITY.name): Group[] {
  const near: Group[] = [];
  for (let i = 0; i < SPECIES.length; i++) {
    const s = SPECIES[i], travel = s.behaviour === 'travel';
    // A loop reaches at most a cell or so from its anchor; a line is in play while its wrap point is out of range.
    const far = travel ? s.speed * TRAVEL_PERIOD / 2 - s.range : s.range + s.cell;
    const x0 = Math.floor((view.x - far) / s.cell) - 1, x1 = Math.floor((view.x + far) / s.cell), z0 = Math.floor((view.z - far) / s.cell) - 1, z1 = Math.floor((view.z + far) / s.cell);
    for (let ix = x0; ix <= x1; ix++) for (let iz = z0; iz <= z1; iz++) {
      const key = ((ix + 32768) * 65536 + (iz + 32768)) * 16 + i;
      let group = cache.get(key);
      if (group === undefined) { group = groupAt(i, ix, iz, city); cache.set(key, group); }
      if (group && Math.hypot(group.x - view.x, group.z - view.z) < (travel ? far : s.range + group.reach)) near.push(group);
    }
  }
  if (cache.size > 6000) cache.clear();
  return near;
}

/** Writes the birds in sight at shared second `t` into `out` (BIRD_STRIDE floats each: position xyz; forward xyz and
 *  bank (rad); span (m, 0 = hidden), wing-beat phase (rad), beat amplitude (rad), chord; body, wing and tip albedo)
 *  and returns their count (at most `max`). */
export function sampleBirds(groups: readonly Group[], view: BirdView, t: number, conditions: BirdConditions, out: Float32Array, max: number): number {
  let n = 0;
  const dt = 0.25;
  for (const group of groups) {
    const s = SPECIES[group.species], active = activity(s, conditions);
    if (active <= 0) continue;
    const travel = s.behaviour === 'travel', anchor = Math.hypot(group.x - view.x, group.z - view.z);
    const near = travel ? 1 - smooth(s.speed * TRAVEL_PERIOD / 2 - s.range - ANCHOR_FADE, s.speed * TRAVEL_PERIOD / 2 - s.range, anchor) : 1;
    if (near <= 0) continue;
    for (const m of group.members) {
      const shown = clamp01((active - m.threshold) / 0.06) * near;
      if (shown <= 0) continue;
      const p = place(m, t), x = p.x, y = p.y + view.ground, z = p.z;
      const distance = Math.hypot(x - view.x, y - view.y, z - view.z), fade = 1 - smooth(s.range * 0.85, s.range, distance);
      if (fade <= 0) continue;
      const before = place(m, t - dt), bx = before.x, by = before.y + view.ground, bz = before.z;
      const after = place(m, t + dt), vx = (after.x - bx) / (2 * dt), vy = (after.y + view.ground - by) / (2 * dt), vz = (after.z - bz) / (2 * dt);
      const speed = Math.hypot(vx, vy, vz) || 1;
      // Bank into the turn: the sideways acceleration against gravity.
      const ax = (after.x - 2 * x + bx) / (dt * dt), az = (after.z - 2 * z + bz) / (dt * dt);
      const bank = Math.max(-1.1, Math.min(1.1, Math.atan2((ax * vz - az * vx) / (Math.hypot(vx, vz) || 1), 9.81)));
      // Wing beats come in bouts between glides; a climbing bird beats, a sinking one glides.
      const bout = s.glide <= 0 ? 1 : smooth(-0.15, 0.15, Math.sin(t * (s.behaviour === 'dart' ? 2.6 : 0.37) + m.rhythm) - (2 * s.glide - 1) + vy * 0.25);
      if (n >= max) return n;
      const o = n++ * BIRD_STRIDE;
      out[o] = x; out[o + 1] = y; out[o + 2] = z;
      out[o + 3] = vx / speed; out[o + 4] = vy / speed; out[o + 5] = vz / speed; out[o + 6] = bank;
      out[o + 7] = s.span * m.size * shown * fade;
      const phase = t * s.flapHz * TAU + m.beat;
      out[o + 8] = phase - Math.floor(phase / TAU) * TAU; out[o + 9] = s.flapAngle * bout; out[o + 10] = s.chord;
      out[o + 11] = s.body; out[o + 12] = s.wing; out[o + 13] = s.tip;
    }
  }
  return n;
}
