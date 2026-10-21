import type { Booking, BookingStatus, Seat, Segment, Trip } from '@prisma/client';

/**
 * Estado mostrado de un asiento CONCRETAMENTE EN UN TRAMO.
 * Un asiento puede estar libre en un tramo y reservado en otro (Teilstrecken).
 */
export type SeatDisplayStatus = 'AVAILABLE' | 'HELD' | 'BOOKED';

/** Reserva activa de un asiento en un tramo (para pintar el mapa del vagon). */
export interface SeatReservationInfo {
  segmentId: string;
  from: string;
  to: string;
  status: BookingStatus;
}

export interface SeatAvailability {
  seatId: string;
  seatNumber: string;
  carNumber: number;
  status: SeatDisplayStatus;
  reservations: SeatReservationInfo[];
}

export interface HoldSeatInput {
  userId: string;
  tripId: string;
  seatId: string;
  segmentId: string;
  ttlSeconds?: number;
}

export type BookingWithRelations = Booking & { trip: Trip; seat: Seat; segment: Segment };
export type BookingWithSeatSegment = Booking & { seat: Seat; segment: Segment };

export interface HoldSeatResult {
  booking: BookingWithRelations;
  ttlSeconds: number;
  expiresAt: Date;
}

/**
 * Puerto de inventario. Encapsula toda la logica transaccional de
 * disponibilidad / retencion / confirmacion de un asiento por tramo.
 *
 * `SimulatedProvider` implementa el motor de verdad de este MVP.
 * `DbProvider` (stub) documenta por donde se conectara la API real de
 * Deutsche Bahn cuando exista un contrato commercial.
 */
export interface InventoryProvider {
  readonly name: 'simulated' | 'db';

  /** Retiene el asiento en el tramo indicado para un usuario (PENDING_PAYMENT). */
  holdSeat(input: HoldSeatInput): Promise<HoldSeatResult>;

  /** Confirma y cobra la reserva de forma instantanea (CONFIRMED). */
  confirmBooking(bookingId: string): Promise<BookingWithSeatSegment>;

  /** Cancela la reserva, libera el asiento y su lock (CANCELLED). */
  cancelBooking(bookingId: string): Promise<BookingWithSeatSegment>;

  /** Vence retenciones PENDING_PAYMENT expiradas (FAILED) y libera asientos. Devuelve cuantas. */
  releaseExpired(): Promise<number>;

  /** Disponibilidad por asiento para el mapa del vagon de un tramo. */
  getSegmentAvailability(segmentId: string): Promise<SeatAvailability[]>;
}