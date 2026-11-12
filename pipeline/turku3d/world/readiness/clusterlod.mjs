// Builds a cluster level-of-detail hierarchy the way virtualised geometry does, to see whether a source mesh lets
// one be built (docs/design/render-readiness.md, G2-G3): meshlets, groups of neighbouring meshlets, each group
// simplified to half with the vertices it shares with other groups locked, then split into meshlets again.
//
//   node clusterlod.mjs MESHOPTIMIZER_DIR JOBS.json   ->  one JSON line per job on stdout
//
// A job is {name, file}: the file holds uint32 vertex and index counts, float32 xyz positions (welded) and uint32
// indices. Nothing is written; the result says what share of the triangles is still needed at three error budgets.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const [moduleDir, jobsPath] = process.argv.slice(2);
const { MeshoptClusterizer } = await import(pathToFileURL(`${moduleDir}/meshopt_clusterizer.js`).href);
const { MeshoptSimplifier } = await import(pathToFileURL(`${moduleDir}/meshopt_simplifier.js`).href);
await MeshoptClusterizer.ready;
await MeshoptSimplifier.ready;

const MAX_VERTICES = 192;
const MAX_TRIANGLES = 128;
const GROUP = 8; // meshlets simplified together
const STUCK = 0.85; // a group that keeps more than this share of its triangles stops there

/** Meshlets of an index list, each as global vertex indices. */
function split(indices, positions) {
  const used = new Map();
  const local = new Uint32Array(indices.length);
  const ids = [];
  for (let i = 0; i < indices.length; i++) {
    let slot = used.get(indices[i]);
    if (slot === undefined) {
      slot = ids.length;
      used.set(indices[i], slot);
      ids.push(indices[i]);
    }
    local[i] = slot;
  }
  const compact = new Float32Array(ids.length * 3);
  for (let i = 0; i < ids.length; i++) compact.set(positions.subarray(ids[i] * 3, ids[i] * 3 + 3), i * 3);
  const buffers = MeshoptClusterizer.buildMeshlets(local, compact, 3, MAX_VERTICES, MAX_TRIANGLES, 0);
  const out = [];
  for (let m = 0; m < buffers.meshletCount; m++) {
    const meshlet = MeshoptClusterizer.extractMeshlet(buffers, m);
    const cluster = new Uint32Array(meshlet.triangles.length);
    for (let i = 0; i < cluster.length; i++) cluster[i] = ids[meshlet.vertices[meshlet.triangles[i]]];
    out.push(cluster);
  }
  return out;
}

/** Groups of up to GROUP clusters: neighbours through shared vertices first, then the nearest loose clusters. */
function partition(clusters, positions) {
  const byVertex = new Map();
  const centres = new Float64Array(clusters.length * 3);
  clusters.forEach((cluster, c) => {
    const seen = new Set(cluster);
    for (const v of seen) {
      const list = byVertex.get(v);
      if (list) list.push(c);
      else byVertex.set(v, [c]);
      for (let k = 0; k < 3; k++) centres[c * 3 + k] += positions[v * 3 + k] / seen.size;
    }
  });
  const shared = clusters.map(() => new Map());
  for (const list of byVertex.values()) {
    if (list.length < 2 || list.length > 32) continue;
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        shared[list[i]].set(list[j], (shared[list[i]].get(list[j]) ?? 0) + 1);
        shared[list[j]].set(list[i], (shared[list[j]].get(list[i]) ?? 0) + 1);
      }
    }
  }
  const group = new Int32Array(clusters.length).fill(-1);
  const groups = [];
  const order = [...clusters.keys()].sort((a, b) => shared[a].size - shared[b].size);
  const loose = [];
  for (const seed of order) {
    if (group[seed] !== -1) continue;
    const members = [seed];
    group[seed] = groups.length;
    const pull = new Map(shared[seed]);
    while (members.length < GROUP) {
      let best = -1;
      let weight = 0;
      for (const [c, w] of pull) {
        if (group[c] === -1 && w > weight) {
          best = c;
          weight = w;
        }
      }
      if (best === -1) break;
      group[best] = groups.length;
      members.push(best);
      for (const [c, w] of shared[best]) pull.set(c, (pull.get(c) ?? 0) + w);
    }
    if (members.length === 1) loose.push(groups.length);
    groups.push(members);
  }
  // Loose clusters (pieces that touch nothing) join the nearest loose ones, so small solids are simplified
  // and pruned together instead of each stopping alone.
  if (loose.length > 1) {
    const sorted = loose
      .map((g) => ({ g, c: groups[g][0] }))
      .sort((a, b) => centres[a.c * 3] - centres[b.c * 3] || centres[a.c * 3 + 2] - centres[b.c * 3 + 2]);
    for (let i = 0; i < sorted.length; i += GROUP) {
      const members = sorted.slice(i, i + GROUP);
      for (const m of members.slice(1)) {
        groups[members[0].g].push(m.c);
        groups[m.g] = [];
      }
    }
  }
  return groups.filter((members) => members.length > 0);
}

function build(positions, indices, flags, budget) {
  const vertexCount = positions.length / 3;
  const source = indices.length / 3;
  let clusters = split(indices, positions);
  const levels = [{ clusters: clusters.length, triangles: source }];
  let stuck = 0;
  let error = 0;
  let passes = 0;
  while (clusters.length > 1 && passes++ < 32) {
    const groups = partition(clusters, positions);
    const owner = new Int32Array(vertexCount).fill(-1);
    const locked = new Uint8Array(vertexCount);
    groups.forEach((members, g) => {
      for (const c of members) {
        for (const v of clusters[c]) {
          if (owner[v] === -1) owner[v] = g;
          else if (owner[v] !== g) locked[v] = 1;
        }
      }
    });
    const next = [];
    let active = 0;
    for (const members of groups) {
      let size = 0;
      for (const c of members) size += clusters[c].length;
      const merged = new Uint32Array(size);
      let at = 0;
      for (const c of members) {
        merged.set(clusters[c], at);
        at += clusters[c].length;
      }
      // Work on the group's own vertices: the simplifier's cost then follows the group, not the mesh.
      const slots = new Map();
      const ids = [];
      const local = new Uint32Array(size);
      for (let i = 0; i < size; i++) {
        let slot = slots.get(merged[i]);
        if (slot === undefined) {
          slot = ids.length;
          slots.set(merged[i], slot);
          ids.push(merged[i]);
        }
        local[i] = slot;
      }
      const points = new Float32Array(ids.length * 3);
      const lock = new Uint8Array(ids.length);
      for (let i = 0; i < ids.length; i++) {
        points.set(positions.subarray(ids[i] * 3, ids[i] * 3 + 3), i * 3);
        lock[i] = locked[ids[i]];
      }
      const target = Math.max(3, Math.floor(size / 6) * 3);
      const [simplified, reached] = MeshoptSimplifier.simplifyWithAttributes(
        local, points, 3, new Float32Array(0), 0, [], lock, target, budget, flags,
      );
      if (simplified.length > size * STUCK) {
        stuck += size / 3;
        continue;
      }
      error = Math.max(error, reached);
      if (simplified.length === 0) continue;
      const global = new Uint32Array(simplified.length);
      for (let i = 0; i < simplified.length; i++) global[i] = ids[simplified[i]];
      active += global.length / 3;
      for (const cluster of split(global, positions)) next.push(cluster);
    }
    levels.push({ clusters: next.length, triangles: active + stuck });
    clusters = next;
    if (active === 0) break;
  }
  const last = levels[levels.length - 1];
  return { levels: levels.length, share: last.triangles / source, error };
}

// Error budgets in metres and the view distance at which each is one pixel (1080p, 60 degrees vertical).
const BUDGETS = [[0.02, '20m'], [0.08, '80m'], [0.3, '300m']];

/** How many of the triangles are still needed at each error budget, with open borders locked (no crack can open:
 * what a watertight result needs) and, for the largest budget, with them free (cracks along T-junctions and
 * unwelded seams accepted). A well-formed mesh needs a few per cent at 300 m either way. */
function measure(positions, indices) {
  const needed = {};
  let levels = 0;
  for (const [budget, name] of BUDGETS) {
    const result = build(positions, indices, ['ErrorAbsolute', 'Prune', 'LockBorder'], budget);
    needed[name] = Number(result.share.toFixed(4));
    levels = Math.max(levels, result.levels);
  }
  const [budget, name] = BUDGETS[BUDGETS.length - 1];
  const free = build(positions, indices, ['ErrorAbsolute', 'Prune'], budget);
  return { sourceTriangles: indices.length / 3, levels, needed, neededFree: { [name]: Number(free.share.toFixed(4)) } };
}

for (const job of JSON.parse(readFileSync(jobsPath, 'utf8'))) {
  let result;
  try {
    const data = readFileSync(job.file);
    const vertexCount = data.readUInt32LE(0);
    const indexCount = data.readUInt32LE(4);
    const positions = new Float32Array(data.buffer.slice(data.byteOffset + 8, data.byteOffset + 8 + vertexCount * 12));
    const indices = new Uint32Array(data.buffer.slice(data.byteOffset + 8 + vertexCount * 12, data.byteOffset + 8 + vertexCount * 12 + indexCount * 4));
    const started = performance.now();
    result = { ...measure(positions, indices), ms: Math.round(performance.now() - started) };
  } catch (error) {
    result = { failed: String(error && error.message ? error.message : error) };
  }
  process.stdout.write(`${JSON.stringify({ name: job.name, ...result })}\n`);
}
