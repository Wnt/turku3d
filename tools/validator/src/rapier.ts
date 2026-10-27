// Both packages are pinned to Rapier 0.21. Their generated classes have separate private
// TypeScript brands despite the same runtime API. Bridge that nominal boundary once;
// the shared game code remains typed against its own declarations throughout.
import Compat from '@dimforge/rapier3d-compat';
import type { RapierModule } from '../../../game/src/vehicle/vehicle.ts';
export const RAPIER = Compat as unknown as RapierModule & Pick<typeof Compat, 'init'>;
