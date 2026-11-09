import { spawn } from 'node:child_process';

// Finish every sibling before surfacing an error to the persistent packager.
// Otherwise a failed request could leave children writing into the next one.
export async function completed(promises) {
  const results = await Promise.allSettled(promises);
  const failure = results.find(result => result.status === 'rejected');
  if (failure) throw failure.reason;
  return results.map(result => result.value);
}

// toktx arguments for linear maps (normals, ORM) in UASTC: rate-distortion optimisation at lambda 1 before zstd.
// Same-pose stills of landmark facades under midday and raking sun could not tell it from plain UASTC, at 40 % of
// the bytes (normals 32.3 -> 15.4 MB, ORM 11.8 -> 2.4 MB for 46 maps; ETC1S flattened stone relief, half resolution
// softened it). It costs ~3.5x the UASTC encode time (loadspeed 2026-11-09, coordinator-approved).
export function uastcLinear(zcmp) {
  return ['--uastc_quality', '2', '--uastc_rdo_l', '1', '--zcmp', String(zcmp)];
}

// Two single-thread codec jobs per packager use the same CPU allowance as the
// former serial two-thread encoder, while extracts and independent maps overlap.
export function codecRunner(limit = 2) {
  let running = 0;
  const waiting = [];
  return async (tool, args) => {
    if (running >= limit) await new Promise(resolve => waiting.push(resolve));
    else running++;
    try {
      await new Promise((resolve, reject) => {
        const child = spawn(tool, args, { stdio:['ignore', 'pipe', 'pipe'] });
        let output = '';
        for (const stream of [child.stdout, child.stderr]) stream.on('data', data => {
          output = (output + data).slice(-8000);
        });
        child.on('error', reject);
        child.on('close', (code, signal) => code === 0 ? resolve()
          : reject(new Error(`${tool}: ${signal ?? code}\n${output}`)));
      });
    } finally {
      const next = waiting.shift();
      if (next) next(); else running--;
    }
  };
}
