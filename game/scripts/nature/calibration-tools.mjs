import { execFileSync } from 'node:child_process';

/** Check the final calibration dependencies before a full build writes any assets. */
export function requireCalibrationTools(run = execFileSync) {
  const tools = [
    ['python3', ['-c', 'import numpy; from PIL import Image'], 'Python 3 with numpy and Pillow'],
    ['ktx', ['--version'], 'KTX-Software ktx'],
    ['toktx', ['--version'], 'KTX-Software toktx'],
  ];
  for (const [command, args, description] of tools) {
    try { run(command, args, { stdio: ['ignore', 'pipe', 'pipe'], timeout: 10000 }); }
    catch {
      throw new Error(`Nature asset build requires ${description} on PATH for foliage alpha calibration. Run through pipeline/env/run.sh after pipeline-bootstrap; no assets have been written.`);
    }
  }
}
