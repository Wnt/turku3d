import fs from 'node:fs';
import path from 'node:path';
const [directory, phase = 'flight'] = process.argv.slice(2);
if (!directory || !['flight', 'drive'].includes(phase)) throw new Error('Usage: summarize.mjs DIRECTORY flight|drive');
const filename = path.join(directory, fs.existsSync(path.join(directory, 'scenario.json')) ? 'scenario.json' : `${phase}-state.json`);
const state = JSON.parse(fs.readFileSync(filename));
const frames = state.frames.filter(row => row[2] === phase);
const sorted = frames.map(row => row[1]).sort((a, b) => a - b);
const percentile = (values, p) => values[Math.max(0, Math.ceil(values.length * p) - 1)] ?? null;
const summary = values => { const ordered = [...values].sort((a, b) => a - b); return { p50: percentile(ordered, .5), p95: percentile(ordered, .95), max: ordered.at(-1) ?? null }; };
const seconds = frames.reduce((sum, frame) => sum + frame[1], 0) / 1000;
const poses = state.poses.filter(pose => pose.phase === phase);
const carPoses = poses.map(pose => pose.state.vehicles.find(vehicle => vehicle.id === 'player')?.pos).filter(Boolean);
const traveled = phase === 'drive' ? carPoses.reduce((sum, pos, i) => sum + (i ? Math.hypot(pos[0] - carPoses[i - 1][0], pos[2] - carPoses[i - 1][2]) : 0), 0) : null;
const samples = JSON.parse(fs.readFileSync(path.join(directory, phase, 'frames.json')));
const heaps = poses.map(pose => pose.heap).filter(Number.isFinite);
const building = state.builders.filter(row => row.phase === phase);
const rtts = state.physics.filter(row => row[3] === phase && Number.isFinite(row[1])).map(row => row[1]);
let allocations = null;
const profile = path.join(directory, phase, 'alloc.heapprofile');
if (fs.existsSync(profile)) {
  const data = JSON.parse(fs.readFileSync(profile));
  const total = node => (node.selfSize ?? 0) + (node.children ?? []).reduce((sum, child) => sum + total(child), 0);
  const bytes = total(data.head);
  const recordingSeconds = samples.at(-1)?.t;
  allocations = { sampledBytes: bytes, recordingSeconds, bytesPerRecordedSecond: bytes / recordingSeconds };
}
console.log(JSON.stringify({ directory, phase, seconds, frames: frames.length, ms: summary(sorted),
  over50ms: sorted.filter(ms => ms > 50).length,
  over100msPerMinute: sorted.filter(ms => ms > 100).length * 60 / seconds,
  publicationAgeMs: summary(frames.map(row => row[4]).filter(Number.isFinite)),
  messageRttMs: summary(rtts),
  heapMiB: { first: heaps[0] / 1024 ** 2, last: heaps.at(-1) / 1024 ** 2, min: Math.min(...heaps) / 1024 ** 2, max: Math.max(...heaps) / 1024 ** 2 },
  scale: [...new Set(poses.map(pose => pose.scale))], canvas: [...new Set(poses.map(pose => pose.canvas.slice(1).join('x')))],
  materialKeys: poses.at(-1)?.materialKeys - poses[0]?.materialKeys, shadowKeys: poses.at(-1)?.shadowKeys - poses[0]?.shadowKeys,
  nodeBuilders: { total: building.length, inFrame: building.filter(row => row.inFrame).length, reflection: building.filter(row => row.reflection).length },
  traveledMeters: traveled, driveFirst: carPoses[0], driveLast: carPoses.at(-1), allocations, errors: state.errors ?? [] }, null, 2));
