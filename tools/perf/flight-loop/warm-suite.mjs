// Deployed acceptance: real input, fresh owned browser profiles, unchanged city caches.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../dev/environment.mjs';
const label = process.argv[2];
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Supply a unique evidence label');
function run(suffix, direction, offset = 3, ready = child => child.send('fly')) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['tools/perf/flight-loop/warm.mjs', `${label}-${suffix}`, direction], {
      stdio: ['inherit', 'inherit', 'inherit', 'ipc'], env: { ...process.env, FLIGHT_PORT_OFFSET: String(offset) },
    });
    child.on('message', message => { if (message.stage === 'flight-ready') ready(child); });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`${suffix}: exit ${code}`)));
  });
}
const outcomes = [];
for (const [suffix, direction] of [['north', 'north'], ['diagonal', 'northeast']]) {
  outcomes.push(...await Promise.allSettled([run(suffix, direction)]));
}
const waiting = [];
const together = child => { waiting.push(child); if (waiting.length === 2) waiting.forEach(player => player.send('fly')); };
outcomes.push(...await Promise.allSettled([run('player-one', 'north', 3, together), run('player-two', 'east', 4, together)]));
const players = ['player-one', 'player-two'].map(suffix => {
  const file = path.join(reportsRoot, 'warm-flight', `${label}-${suffix}`, 'result.json');
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
});
if (players.every(Boolean)) {
  const overlapMs = Math.min(...players.map(p => p.startedAt + p.summary.totalMs)) - Math.max(...players.map(p => p.startedAt));
  console.log(JSON.stringify({ stage: 'simultaneous-flight', overlapMs }));
  if (overlapMs < 10000) throw new Error('Flights did not overlap for ten seconds');
}
const failures = outcomes.filter(result => result.status === 'rejected').map(result => result.reason);
if (failures.length) throw new AggregateError(failures, 'Deployed flight acceptance failed');
