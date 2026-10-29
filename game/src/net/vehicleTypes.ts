import type { V3 } from '../core/origin';
import type { NetPose } from './client';

export interface SharedVehicle {
  id: string; source: string | null; creator: string; localId: string;
  owner: string; occupant: string | null; epoch: number; revision: number; half: V3; pose: NetPose;
}
export interface VehicleDelta { patches?: { id: string; revision: number; pose: Partial<NetPose> }[]; upsert: SharedVehicle[]; remove: string[]; sources?: string[] }
export interface VehicleUpdate { id: string; epoch: number; pose: NetPose }
export interface VehicleCommand {
  action: 'register' | 'acquire' | 'release'; id: string; source?: string | null;
  localId?: string; half?: V3; pose?: NetPose;
}
