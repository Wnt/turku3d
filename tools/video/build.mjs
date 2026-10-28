// Build the unmodified game and a capture-only entry with shared three.js modules.
import { build } from '../../game/node_modules/vite/dist/node/index.js';
import path from 'node:path';
await build({ root: path.resolve('game'), configFile: path.resolve('game/vite.config.ts'),
  build: { manifest: true, rollupOptions: {
    input: { game: path.resolve('game/index.html'), video: path.resolve('tools/video/runtime.ts') },
    preserveEntrySignatures: 'strict', output: { entryFileNames: 'assets/[name].js' },
  } } });
