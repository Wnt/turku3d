import { defineConfig, mergeConfig } from 'vite';
import base from './vite.config';
import path from 'node:path';

export default mergeConfig(base, defineConfig({
  server: { fs: { allow: [path.resolve('.'), ...(process.env.TURKU3D_WORK ? [process.env.TURKU3D_WORK] : [])] } },
}));
