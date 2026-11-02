import type { FleetModel } from './catalog';

export interface AuthoredModel {
  id: string;
  assetBase: string;
  fleet: FleetModel;
  triangles: number[];
  wheels: { radius: number; width: number; pivots: Record<'wheel_FL'|'wheel_FR'|'wheel_RL'|'wheel_RR', [number,number,number]> };
  spec: Record<string, unknown>;
  accepted: boolean;
}
// One file per model avoids concurrent roster writes by the two modelling loops.
// Candidate models are available in their candidate checkout for the street judgement.
export const AUTHORED_MODELS = Object.values(import.meta.glob<AuthoredModel>('./models/*.json', { eager: true, import: 'default' }));
export const AUTHORED_BY_ID = new Map(AUTHORED_MODELS.map(model => [model.id, model]));
