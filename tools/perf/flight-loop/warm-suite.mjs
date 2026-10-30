// Deployed acceptance: real input, fresh owned browser profiles, unchanged city caches.
import { spawn } from 'node:child_process';
const label = process.argv[2];
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Supply a unique evidence label');
function run(suffix, direction, offset = 3) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['tools/perf/flight-loop/warm.mjs', `${label}-${suffix}`, direction], {
      stdio: 'inherit', env: { ...process.env, FLIGHT_PORT_OFFSET: String(offset) },
    });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`${suffix}: exit ${code}`)));
  });
}
const outcomes = [];
for (const [suffix, direction] of [['north', 'north'], ['diagonal', 'northeast']]) {
  outcomes.push(...await Promise.allSettled([run(suffix, direction)]));
}
outcomes.push(...await Promise.allSettled([run('player-one', 'north', 3), run('player-two', 'east', 4)]));
const failures = outcomes.filter(result => result.status === 'rejected').map(result => result.reason);
if (failures.length) throw new AggregateError(failures, 'Deployed flight acceptance failed');
