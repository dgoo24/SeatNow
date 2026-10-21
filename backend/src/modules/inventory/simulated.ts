import { randomUUID } from 'node:crypto';
import { prisma } from '../../shared/database/prisma';
import { env } from '../../config/env';
import { ConflictError } from '../../shared/errors';
import { sseBroker } from '../../shared/sse/sse-broker';
import {
  acquireResourceLock,
  getResourceLockTtl,
  releaseResourceLock,
} from '../../shared/redis/locks';
import type {
  HoldSeatInput,
  HoldSeatResult,
  InventoryProvider,
  SeatAvailability,
  SeatDisplayStatus,
} from './types';

const ACTIVE = ['PENDING_PAYMENT', 'CONFIRMED'] as const;

/** Key de lock Redis: un recurso = (asiento, tramo). */
const resourceKey = (seatId: string, segmentId: string) => `seat:${seatId}:seg:${segmentId}`;

/** Dos intervalos de secuencias de parada se solapan si comparten al menos una parada. */
export function intervalsOverlap(a: { from: number; to: number }, b: { from: number; to: number }): boolean {
  return a.from < b.to && b.from < a.to;
}

function seatEvent(
  seatId: string,
  segmentId: string,
  status: SeatDisplayStatus,
  bookingId?: string,
) {
  return {
    seatId,
    segmentId,
    status,
    ...(bookingId ? { bookingId } : {}),
    at: new Date().toISOString(),
  };
}

export class SimulatedProvider implements InventoryProvider {
  readonly name = 'simulated' as const;

  async holdSeat(input: HoldSeatInput): Promise<HoldSeatResult> {
    const ttlSeconds = input.ttlSeconds ?? env.SEAT_HOLD_TTL_SECONDS;
    const bookingId = randomUUID();

    const seat = await prisma.seat.findFirst({
      where: { id: input.seatId, tripId: input.tripId },
    });
    if (!seat) throw new ConflictError('Der Sitzplatz gehört nicht zu dieser Fahrt');

    const segment = await prisma.segment.findFirst({
      where: { id: input.segmentId, tripId: input.tripId },
    });
    if (!segment) throw new ConflictError('Die Teilstrecke gehört nicht zu dieser Fahrt');

    // Competicion por el mismo (asiento, tramo): ganador unico via SET NX EX.
    const acquired = await acquireResourceLock(resourceKey(seat.id, segment.id), {
      userId: input.userId,
      bookingId,
      ttlSeconds,
    });
    if (!acquired) throw new ConflictError('Der Sitzplatz in dieser Teilstrecke wird gerade von einem anderen Fahrgast reserviert');

    // Chequeo de solape en DB: el mismo asiento ya puede estar reservado
    // en un tramo que se solapa (protege Teilstrecken sobre el lock de Redis).
    const conflicts = await prisma.booking.findMany({
      where: {
        seatId: seat.id,
        status: { in: [...ACTIVE] },
        segment: { tripId: input.tripId },
      },
      include: { segment: true },
    });
    const overlapping = conflicts.find((b) =>
      intervalsOverlap(
        { from: segment.originSequence, to: segment.destinationSequence },
        { from: b.segment.originSequence, to: b.segment.destinationSequence },
      ),
    );
    if (overlapping) {
      await releaseResourceLock(resourceKey(seat.id, segment.id), bookingId);
      throw new ConflictError(
        `Der Sitzplatz ${seat.seatNumber} ist bereits auf der überlappenden Teilstrecke reserviert ` +
          `${overlapping.segment.originStopName} -> ${overlapping.segment.destinationStopName}`,
      );
    }

    const expiresAt = new Date(Date.now() + ttlSeconds * 1000);
    const booking = await prisma.booking.create({
      data: {
        id: bookingId,
        userId: input.userId,
        tripId: input.tripId,
        seatId: seat.id,
        segmentId: segment.id,
        status: 'PENDING_PAYMENT',
        totalAmount: segment.price,
        expiresAt,
      },
      include: { trip: true, seat: true, segment: true },
    });

    sseBroker.broadcastToTrip(input.tripId, 'seat:update', {
      ...seatEvent(seat.id, segment.id, 'HELD', booking.id),
      seatNumber: seat.seatNumber,
      segmentFrom: segment.originStopName,
      segmentTo: segment.destinationStopName,
    });

    return { booking, ttlSeconds, expiresAt };
  }

  async confirmBooking(bookingId: string) {
    const booking = await prisma.booking.findUnique({
      where: { id: bookingId },
      include: { seat: true, segment: true },
    });
    if (!booking) throw new ConflictError('Buchung nicht gefunden');
    if (booking.status !== 'PENDING_PAYMENT') {
      throw new ConflictError(`Die Buchung ist nicht mehr zur Zahlung offen (Status ${booking.status})`);
    }

    const confirmed = await prisma.booking.update({
      where: { id: bookingId },
      data: { status: 'CONFIRMED', expiresAt: null },
      include: { seat: true, segment: true },
    });

    await releaseResourceLock(resourceKey(booking.seatId, booking.segmentId), bookingId);

    sseBroker.broadcastToTrip(booking.tripId, 'seat:update', {
      ...seatEvent(booking.seatId, booking.segmentId, 'BOOKED', booking.id),
      seatNumber: booking.seat.seatNumber,
    });

    return confirmed;
  }

  async cancelBooking(bookingId: string) {
    const booking = await prisma.booking.findUnique({
      where: { id: bookingId },
      include: { seat: true, segment: true },
    });
    if (!booking) throw new ConflictError('Buchung nicht gefunden');

    const cancelled = await prisma.booking.update({
      where: { id: bookingId },
      data: { status: 'CANCELLED', expiresAt: null, failureReason: 'vom Benutzer storniert' },
      include: { seat: true, segment: true },
    });

    await releaseResourceLock(resourceKey(booking.seatId, booking.segmentId), bookingId);

    sseBroker.broadcastToTrip(booking.tripId, 'seat:update', {
      ...seatEvent(booking.seatId, booking.segmentId, 'AVAILABLE', booking.id),
      seatNumber: booking.seat.seatNumber,
    });

    return cancelled;
  }

  async releaseExpired(): Promise<number> {
    const expired = await prisma.booking.findMany({
      where: { status: 'PENDING_PAYMENT', expiresAt: { lt: new Date() } },
      include: { seat: true, segment: true },
    });

    let released = 0;
    for (const booking of expired) {
      await prisma.booking.update({
        where: { id: booking.id },
        data: { status: 'FAILED', failureReason: 'Reservierung abgelaufen' },
      });
      await releaseResourceLock(resourceKey(booking.seatId, booking.segmentId), booking.id);
      sseBroker.broadcastToTrip(booking.tripId, 'seat:update', {
        ...seatEvent(booking.seatId, booking.segmentId, 'AVAILABLE', booking.id),
        seatNumber: booking.seat.seatNumber,
      });
      released += 1;
    }
    return released;
  }

  async getSegmentAvailability(segmentId: string): Promise<SeatAvailability[]> {
    const segment = await prisma.segment.findUnique({ where: { id: segmentId } });
    if (!segment) throw new ConflictError('Teilstrecke nicht gefunden');

    const [seats, bookings] = await Promise.all([
      prisma.seat.findMany({
        where: { tripId: segment.tripId },
        orderBy: [{ carNumber: 'asc' }, { seatNumber: 'asc' }],
      }),
      prisma.booking.findMany({
        where: {
          tripId: segment.tripId,
          status: { in: [...ACTIVE] },
        },
        include: { segment: true },
      }),
    ]);

    return seats.map((seat) => {
      const owned = bookings.filter((b) => b.seatId === seat.id);
      const reservations = owned.map((b) => ({
        segmentId: b.segmentId,
        from: b.segment.originStopName,
        to: b.segment.destinationStopName,
        status: b.status,
      }));
      const segmentReservation = owned.find((b) =>
        intervalsOverlap(
          { from: segment.originSequence, to: segment.destinationSequence },
          { from: b.segment.originSequence, to: b.segment.destinationSequence },
        ),
      );
      const status: SeatDisplayStatus = segmentReservation
        ? segmentReservation.status === 'PENDING_PAYMENT'
          ? 'HELD'
          : 'BOOKED'
        : 'AVAILABLE';
      return {
        seatId: seat.id,
        seatNumber: seat.seatNumber,
        carNumber: seat.carNumber,
        status,
        reservations,
      };
    });
  }

  async lockRemainingSeconds(seatId: string, segmentId: string): Promise<number> {
    return getResourceLockTtl(resourceKey(seatId, segmentId));
  }
}