// The building workbench's dev server (scripts/dev/building-workbench.mjs): the game's own Vite configuration on the
// sandbox's probe port, allowed to read the workbench output and the reference photos, which stay outside Git.
import { defineConfig, mergeConfig } from 'vite';
import base from './vite.config';
import os from 'node:os';
import path from 'node:path';
import { port, workRoot } from '../tools/dev/environment.mjs';

const refs = path.resolve(process.env.TURKU3D_DATA ?? path.join(os.homedir(), 'turku3d/data'), 'qa/refs');
const workbench = path.join(workRoot, 'workbench');

export default mergeConfig(base, defineConfig({
  define: { __WORKBENCH_ROOT__: JSON.stringify(workbench), __WORKBENCH_REFS__: JSON.stringify(refs) },
  server: { port: port(3), fs: { allow: [path.resolve(import.meta.dirname), workbench, refs] } },
}));
