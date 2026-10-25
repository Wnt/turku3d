// Validator thresholds and CLI options (ARCHITECTURE Task 1.11).
import { DEFAULT_KCC, type KccConfig } from './kcc.ts';

export const CHECKS = ['format', 'seams', 'cars', 'holes', 'buildings', 'curbs', 'stairs', 'belowGround'] as const;
export type CheckName = (typeof CHECKS)[number];

export interface Options {
  inputs: string[];
  footprints?: string;
  curbs?: string;
  stairs?: string;
  layers?: string;
  workers?: number;
  coreTile?: [number, number];
  metadata?: { footprints: any; curbs: any; stairs: any[]; sources: any[] };
  checks?: CheckName[];
  seed: number;
  walkers: number;
  walkSeconds: number;
  rayDensity: number; // rays per m² (spec: 1 ray per 0.5 m² = 2)
  dropSpacing: number;
  curbSpacing: number;
  curbSpeeds: number[];
  climbRequired: number; // steps <= this must be climbed (Task 1.11c: 0.25)
  blockRequired: number; // steps >= this must block (Task 1.6/1.13: 0.35)
  stairMaxSlopeDeg: number;
  kcc: KccConfig;
  aoi?: [number, number, number, number];
  maxSamples: number;
}

export const DEFAULT_OPTIONS: Omit<Options, 'inputs'> = {
  seed: 1,
  walkers: 2000,
  walkSeconds: 10,
  rayDensity: 2,
  dropSpacing: 5,
  curbSpacing: 2,
  curbSpeeds: [1.5, 4.0],
  climbRequired: 0.25,
  blockRequired: 0.35,
  stairMaxSlopeDeg: 40,
  kcc: DEFAULT_KCC,
  maxSamples: 20,
};
