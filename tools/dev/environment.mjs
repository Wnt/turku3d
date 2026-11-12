// Host-side paths and ports shared by Vite, Playwright, QA and the dist server.
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

const dataRoot = path.resolve(process.env.TURKU3D_DATA ?? path.join(os.homedir(), 'turku3d/data'));
export const workRoot = path.resolve(process.env.TURKU3D_WORK ?? dataRoot);
/** The canonical content-addressed prepared scene objects (also packaged by tools/share/deploy.sh). */
export const preparedAssets = path.resolve(process.env.TURKU3D_PREPARED_ASSETS ?? path.join(dataRoot, 'prepared-assets/shared'));
/** The output store of the world pipeline: content-addressed objects and location manifests (docs/design/world-pipeline.md). */
export const worldStore = path.resolve(process.env.TURKU3D_WORLD_STORE ?? path.join(workRoot, 'world'));
export const gameDist = path.join(workRoot, 'build/game');
export const checkGameDist = path.join(workRoot, 'build/check-game');
export const tileDist = process.env.TILE_DIST ?? path.join(workRoot, 'dist/tiles');
export const reportsRoot = path.join(workRoot, 'reports');
const browserRoot = path.resolve(process.env.TURKU3D_BROWSER_DIR ?? path.join(os.homedir(), 'turku3d/browser', process.env.TURKU3D_SANDBOX ?? 'main'));
export function port(offset = 0) {
  const base = Number(process.env.TURKU3D_PORT_BASE ?? 5170);
  if (!Number.isInteger(base) || base < 1024 || base + 9 > 65535 || offset < 0 || offset > 9) throw new Error('invalid TURKU3D_PORT_BASE or offset');
  return base + offset;
}
export function isolateBrowser() {
  // Playwright creates unique Chrome profiles under os.tmpdir(); put them inside this sandbox.
  const temp = path.join(browserRoot, 'tmp');
  fs.mkdirSync(temp, { recursive: true });
  process.env.TMPDIR = temp;
  process.env.TMP = temp;
  process.env.TEMP = temp;
}

export const jobs = Number(process.env.TURKU3D_JOBS ?? Math.max(2, Math.floor(os.cpus().length / 3)));
if (!Number.isSafeInteger(jobs) || jobs < 1) throw new Error('TURKU3D_JOBS must be a positive integer');
