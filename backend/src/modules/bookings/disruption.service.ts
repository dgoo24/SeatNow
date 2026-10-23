/**
 * Disrupciones proactivas de trafico (Zugausfall / kritische Verspaetung):
 *
 * Cuando el modulo realtime detecta un CANCELLED en un viaje (o un retraso
 * critico >= CRITICAL_DELAY_MINUTES), este servicio revisa las reservas
 * CONFIRMED de ese viaje y:
 *
 *  1. Cancelacion: intenta reasignar cada reserva al siguiente viaje de la
 *     MISMA ruta con igual Teilstrecke (origen/destino) y disponibilidad.
 *     - Si hay plaza: la reserva vieja se cancela y se crea una CONFIRMED en
 *       el nuevo viaje (mismo asiento si existe, si no el libre equivalente),
 *       con SSE + email de "umgebucht".
 *  2. Si no hay alternativa: la reserva pasa a CANCEL_PENDING_REFUND
 *     (reembolso/compensacion pendiente) + SSE + email con opciones.
 *  3. Retraso critico (sin cancelacion): notifica via SSE + email sin tocar
 *     el estado de la reserva.
 *
 * El disparo se hace desde realtime.service (poller/REST) con dedupe en Redis
 * para no re-procesar el mismo viaje en cada tick.
 */

import { randomUUID } from 'node:crypto';
import type { Booking, Seat, Segment, Trip, User } from '@prisma/client';
import { prisma } from '../../shared/database/prisma';
import { redis } from '../../shared/redis/redis';
import { env } from '../../config/env';
import { sseBroker } from '../../shared/sse/sse-broker';
import { releaseResourceLock } from '../../shared/redis/locks';
import {
  bookingCancelledHtml,
  bookingReassignedHtml,
  criticalDelayHtml,
  sendMail,
} from '../../shared/email/mailer';
import { inventoryProvider } from '../inventory';
import { berlinDateStr, berlinDayUtcRange, berlinNowMinutes, formatClockTime } from '../../shared/time/berlin';

type BookingFull = Booking & { seat: Seat; segment: Segment; trip: Trip; user: User };

export interface DisruptionInfo {
  cancelled: boolean;
  delayMinutes?: number | null;
}

export interface DisruptionResult {
  handled: boolean;
  reason: 'duplicate' | 'no_bookings' | 'cancelled' | 'critical_delay';
  scanned: number;
  reassigned: number;
  pendingRefund: number;
  notified: number;
}

/** Dedupe: no re-procesar la misma disrupcion del viaje durante horas. */
async function acquireDisruptionLock(tripId: string, kind: string): Promise<boolean> {
  const key = `trip:disruption:${kind}:${tripId}`;
  const res = await redis.set(key, new Date().toISOString(), 'EX', 6 * 3600, 'NX');
  return res === 'OK';
}

function newQrPayload(bookingId: string): string {
  return `TKT-${bookingId.replace(/-/g, '').slice(0, 16).toUpperCase()}`;
}

/**
 * Siguiente viaje de la MISMA ruta con la misma Teilstrecke libre.
 * Prioriza: (1) mismo numero de asiento, (2) cualquier asiento libre.
 */
async function findReplacement(booking: BookingFull): Promise<{
  trip: Trip;
  segment: Segment;
  seat: Seat;
} | null> {
  const today = berlinDateStr();
  const { start, end } = berlinDayUtcRange(today);
  // La salida del sustituto debe ser posterior a "ahora - 15 min" (string HH:MM:SS).
  const minDeparture = (() => {
    const nowMin = Math.max(0, berlinNowMinutes() - 15);
    const h = String(Math.floor(nowMin / 60)).padStart(2, '0');
    const m = String(nowMin % 60).padStart(2, '0');
    return `${h}:${m}:00`;
  })();

  const segments = await prisma.segment.findMany({
    where: {
      tripId: { not: booking.tripId },
      originStopName: booking.segment.originStopName,
      destinationStopName: booking.segment.destinationStopName,
      departureTime: { gte: minDeparture },
      trip: { routeName: booking.trip.routeName },
    },
    include: { trip: { include: { serviceDates: true } } },
    orderBy: { departureTime: 'asc' },
    take: 40,
  });

  const provider = inventoryProvider();
  for (const segment of segments) {
    const runsToday = segment.trip.serviceDates.some((s) => berlinDateStr(s.serviceDate) === today);
    if (!runsToday) continue;

    let availability;
    try {
      availability = await provider.getSegmentAvailability(segment.id);
    } catch {
      continue;
    }

    const sameSeat = availability.find(
      (a) => a.status === 'AVAILABLE' && a.seatNumber === booking.seat.seatNumber && a.carNumber === booking.seat.carNumber,
    );
    if (sameSeat) {
      const seat = await prisma.seat.findUnique({ where: { id: sameSeat.seatId } });
      if (seat) return { trip: segment.trip, segment, seat };
    }

    const anyFree = availability.find((a) => a.status === 'AVAILABLE');
    if (anyFree) {
      const seat = await prisma.seat.findUnique({ where: { id: anyFree.seatId } });
      if (seat) return { trip: segment.trip, segment, seat };
    }
  }
  return null;
}

async function notifyEmail(
  booking: BookingFull,
  build: (d: Parameters<typeof bookingCancelledHtml>[0]) => string,
  subject: string,
): Promise<boolean> {
  if (!booking.user.email) return false;
  return sendMail({
    to: booking.user.email,
    subject,
    html: build({
      fullName: booking.user.fullName,
      origin: booking.segment.originStopName,
      destination: booking.segment.destinationStopName,
      departureTime: formatClockTime(booking.segment.departureTime),
      seatNumber: booking.seat.seatNumber,
      carNumber: booking.seat.carNumber,
      qrPayload: booking.qrPayload,
      oldDepartureTime: formatClockTime(booking.segment.departureTime),
      bookingId: booking.id,
    }),
  });
}

async function reassignBooking(booking: BookingFull): Promise<'reassigned' | 'pending_refund'> {
  const replacement = await findReplacement(booking);

  // Liberar la reserva vieja en cualquier caso (asiento del tren cancelado).
  await releaseResourceLock(`seat:${booking.seatId}:seg:${booking.segmentId}`, booking.id);
  sseBroker.broadcastToTrip(booking.tripId, 'seat:update', {
    seatId: booking.seatId,
    segmentId: booking.segmentId,
    status: 'AVAILABLE',
    bookingId: booking.id,
    seatNumber: booking.seat.seatNumber,
    reason: 'trip_cancelled',
    at: new Date().toISOString(),
  });

  if (replacement) {
    const newBookingId = randomUUID();
    const qrPayload = newQrPayload(newBookingId);
    const created = await prisma.booking.create({
      data: {
        id: newBookingId,
        userId: booking.userId,
        tripId: replacement.trip.id,
        seatId: replacement.seat.id,
        segmentId: replacement.segment.id,
        status: 'CONFIRMED',
        totalAmount: replacement.segment.price,
        qrPayload,
        isCheckedIn: false,
        stripePaymentIntentId: booking.stripePaymentIntentId,
      },
      include: { seat: true, segment: true, trip: true, user: true },
    });

    await prisma.booking.update({
      where: { id: booking.id },
      data: {
        status: 'CANCELLED',
        failureReason: `Zugausfall – automatisch umgebucht auf ${replacement.trip.routeName} ${formatClockTime(replacement.segment.departureTime)} (${replacement.segment.originStopName} -> ${replacement.segment.destinationStopName})`,
        expiresAt: null,
      },
    });

    sseBroker.broadcastToTrip(created.tripId, 'seat:update', {
      seatId: created.seatId,
      segmentId: created.segmentId,
      status: 'BOOKED',
      bookingId: created.id,
      seatNumber: created.seat.seatNumber,
      reason: 'reassigned',
      at: new Date().toISOString(),
    });
    const reassignedMsg = {
      bookingId: booking.id,
      newBookingId: created.id,
      status: 'REASSIGNED',
      newTripId: created.tripId,
          newDepartureTime: formatClockTime(created.segment.departureTime),
      newOrigin: created.segment.originStopName,
      newDestination: created.segment.destinationStopName,
      newSeatNumber: created.seat.seatNumber,
      newCarNumber: created.seat.carNumber,
      message: `Dein Zug ist ausgefallen. Neue Verbindung: ${created.segment.originStopName} -> ${created.segment.destinationStopName}, Abfahrt ${formatClockTime(created.segment.departureTime)}, Platz ${created.seat.seatNumber}.`,
    };
    sseBroker.sendToUser(booking.tripId, booking.userId, 'booking:update', reassignedMsg);
    sseBroker.sendToUser(created.tripId, booking.userId, 'booking:update', reassignedMsg);

    void notifyEmail(
      booking,
      (d) =>
        bookingReassignedHtml({
          ...d,
      newDepartureTime: formatClockTime(created.segment.departureTime),
          newOrigin: created.segment.originStopName,
          newDestination: created.segment.destinationStopName,
          newSeatNumber: created.seat.seatNumber,
          newCarNumber: created.seat.carNumber,
        }),
      `Zugausfall: ${booking.segment.originStopName} -> ${booking.segment.destinationStopName} – automatisch umgebucht`,
    ).catch(() => false);

    return 'reassigned';
  }

  const updated = await prisma.booking.update({
    where: { id: booking.id },
    data: {
      status: 'CANCEL_PENDING_REFUND',
      failureReason: 'Zugausfall – keine alternative Verbindung verfügbar, Rückerstattung ausstehend',
      expiresAt: null,
    },
  });
  void updated;
  sseBroker.sendToUser(booking.tripId, booking.userId, 'booking:update', {
    bookingId: booking.id,
    status: 'CANCEL_PENDING_REFUND',
    message:
      `Dein Zug ${booking.segment.originStopName} -> ${booking.segment.destinationStopName} ist ausgefallen. ` +
      'Keine alternative Verbindung gefunden – Rückerstattung/Kompensation ausstehend.',
  });

  void notifyEmail(
    booking,
    (d) =>
      bookingCancelledHtml(d, [
        'Automatische Rückerstattung des Ticketpreises (2–5 Werktage)',
        'Gutschein über 120 % des Ticketwerts für eine zukünftige Buchung',
        'BahnComfort-Punkte als Entschädigung (bitte beim Support melden)',
      ]),
    `Zugausfall: ${booking.segment.originStopName} -> ${booking.segment.destinationStopName} – deine Optionen`,
  ).catch(() => false);

  return 'pending_refund';
}

async function notifyCriticalDelay(booking: BookingFull, delayMinutes: number): Promise<void> {
  sseBroker.sendToUser(booking.tripId, booking.userId, 'booking:update', {
    bookingId: booking.id,
    status: 'DISRUPTION_NOTICE',
    delayMinutes,
    message: `Dein Zug hat aktuell ${delayMinutes} Minuten Verspätung. Deine Reservierung bleibt bestehen.`,
  });
  void notifyEmail(
    booking,
    (d) => criticalDelayHtml(d, delayMinutes),
    `Verspätung: ${booking.segment.originStopName} -> ${booking.segment.destinationStopName}`,
  ).catch(() => false);
}

/**
 * Punto de entrada llamado por el modulo realtime cuando detecta una
 * cancelacion o un retraso critico en un viaje.
 */
export async function handleTripDisruption(
  tripId: string,
  info: DisruptionInfo,
  options?: { force?: boolean },
): Promise<DisruptionResult> {
  const kind = info.cancelled ? 'cancel' : 'delay';
  if (!options?.force) {
    const fresh = await acquireDisruptionLock(tripId, kind);
    if (!fresh) {
      return { handled: false, reason: 'duplicate', scanned: 0, reassigned: 0, pendingRefund: 0, notified: 0 };
    }
  }

  const bookings = await prisma.booking.findMany({
    where: { tripId, status: 'CONFIRMED' },
    include: { seat: true, segment: true, trip: true, user: true },
    take: 500,
  });

  if (bookings.length === 0) {
    return { handled: true, reason: 'no_bookings', scanned: 0, reassigned: 0, pendingRefund: 0, notified: 0 };
  }

  if (!info.cancelled) {
    const delay = info.delayMinutes ?? 0;
    for (const booking of bookings) {
      await notifyCriticalDelay(booking as BookingFull, delay);
    }
    return { handled: true, reason: 'critical_delay', scanned: bookings.length, reassigned: 0, pendingRefund: 0, notified: bookings.length };
  }

  let reassigned = 0;
  let pendingRefund = 0;
  for (const booking of bookings) {
    const outcome = await reassignBooking(booking as BookingFull);
    if (outcome === 'reassigned') reassigned += 1;
    else pendingRefund += 1;
  }

  return { handled: true, reason: 'cancelled', scanned: bookings.length, reassigned, pendingRefund, notified: 0 };
}

/** Solo cancelaciones confirmadas y retrasos >= umbral disparan el flujo. */
export function shouldHandleRealtime(dis: { cancelled: boolean; delayMinutes?: number | null }): boolean {
  if (dis.cancelled) return true;
  return (dis.delayMinutes ?? 0) >= env.CRITICAL_DELAY_MINUTES;
}
