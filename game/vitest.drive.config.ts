import { defineConfig } from 'vitest/config';
import config from './vitest.config';

// The drive sweep (tests/drive): one long test per shard, output on the console as it goes. The unit config's
// include is replaced, not merged (mergeConfig concatenates arrays).
export default defineConfig({ ...config, test: { ...config.test, include: ['tests/drive/**/*.test.ts'], maxWorkers: 1, testTimeout: 24 * 3600_000 } });
