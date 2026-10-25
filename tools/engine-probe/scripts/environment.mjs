import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, isolateBrowser } from '../../dev/environment.mjs';
export { port } from '../../dev/environment.mjs';

isolateBrowser();
const screenshots = path.join(reportsRoot, 'engine-probe');
fs.mkdirSync(screenshots, { recursive: true });
export const screenshot = (name) => path.join(screenshots, name);
