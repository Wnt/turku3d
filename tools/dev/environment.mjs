// Host-side paths and ports shared by Vite, Playwright, QA and the dist server.
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

const dataRoot = path.resolve(process.env.TURKU3D_DATA ?? path.join(os.homedir(), 'turku3d/data'));
export const workRoot = path.resolve(process.env.TURKU3D_WORK ?? dataRoot);
export const gameDist = path.join(workRoot, 'build/game');
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
