import { AppError } from '../../shared/errors';
import type { HoldSeatInput, HoldSeatResult, InventoryProvider, SeatAvailability } from './types';

/**
 * Stub simultaneo a la API real de Deutsche Bahn (salida en otra version).
 * Documenta el punto de integracion: cuando exista un contrato commercial,
 * este proveedor consultara/holdeara/confirmara contra el sistema de DB.
 */
export class DbProvider implements InventoryProvider {
  readonly name = 'db' as const;

  private unavailable(method: string): never {
    throw new AppError(
      `El proveedor "db" (API real de Deutsche Bahn) no esta implementado. Usa INVENTORY_PROVIDER=simulated. (${method})`,
      501,
      'NOT_IMPLEMENTED',
    );
  }

  holdSeat(_input: HoldSeatInput): Promise<HoldSeatResult> {
    return this.unavailable('holdSeat');
  }

  confirmBooking(_bookingId: string): never {
    return this.unavailable('confirmBooking');
  }

  cancelBooking(_bookingId: string): never {
    return this.unavailable('cancelBooking');
  }

  releaseExpired(): Promise<number> {
    return this.unavailable('releaseExpired');
  }

  getSegmentAvailability(_segmentId: string): Promise<SeatAvailability[]> {
    return this.unavailable('getSegmentAvailability');
  }
}