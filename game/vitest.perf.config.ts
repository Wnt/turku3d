import { defineConfig, mergeConfig } from 'vitest/config';
import config from './vitest.config';

export default mergeConfig(config, defineConfig({ test: { include: ['tests/perf/**/*.test.ts'], maxWorkers: 1 } }));
