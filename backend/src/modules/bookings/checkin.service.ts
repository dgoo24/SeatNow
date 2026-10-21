/**
 * Comfort Check-In / Regla de los 15 minutos (No-Show):
 *
 * - El pasajero valida su presencia via POST /api/bookings/:id/check-in
 *   (simula el escaneo del QR del boarding pass o el boton "Einsteigen" en la app).
 * - Un barrido periodico revisa reservas CONFIRMED sin check-in cuya parada de
 *   origen ya salio hace mas de NO_SHOW_GRACE_MINUTES: la reserva pasa a NO_SHOW
 *   y el asiento queda AVAILABLE para los segmentos restantes del trayecto
 *   (la disponibilidad por Teilstrecke se deriva de los estados de las reservas).
 * - La liberacion se difunde via SSE (seat:update AVAILABLE + booking:update).
 */

import { prisma } from '../../shared/database/prisma';
import { env } from '../../config/env';
import { ConflictError, ForbiddenError, NotFoundError } from '../../shared/errors';
import { sseBroker } from '../../shared/sse/sse-broker';
import { releaseResourceLock } from '../../shared/redis/locks';
import { berlinDateStr, epochOfBerlinLocal } from '../../shared/time/berlin';

/** Ventana maxima tras la salida en la que aun tiene sentido liberar por No-Show. */
const NO_SHOW_MAX_AGE_MS = 6 * 60 * 60 * 1000;

export interface CheckInResult {
  bookingId: string;
  checkedIn: boolean;
  checkedInAt: Date;
  seatNumber: string;
  carNumber: number;
}

/** Check-in digital del pasajero (escaneo QR / boton en la app). */
export async function checkInBooking(
  userId: string,
  bookingId: string,
  qrPayload?: string,
): Promise<CheckInResult> {
  const booking = await prisma.booking.findUnique({
    where: { id: bookingId },
    include: { seat: true, segment: true },
  });
  if (!booking || booking.userId !== userId) throw new NotFoundError('Buchung nicht gefunden');

  if (booking.status === 'NO_SHOW') {
    throw new ConflictError(
      'Der Sitzplatz wurde wegen No-Show freigegeben. Bitte buche eine neue Verbindung.',
    );
  }
  if (booking.status !== 'CONFIRMED') {
    throw new ConflictError(`Check-in nur für bestätigte Buchungen möglich (Status ${booking.status})`);
  }

  // Escaneo del QR del boarding pass: si se envía, debe coincidir.
  if (qrPayload && booking.qrPayload && qrPayload !== booking.qrPayload) {
    throw new ForbiddenError('QR-Code stimmt nicht mit dieser Buchung überein');
  }

  if (booking.isCheckedIn && booking.checkedInAt) {
    // Idempotente: un doble escaneo no cambia nada.
    return {
      bookingId: booking.id,
      checkedIn: true,
      checkedInAt: booking.checkedInAt,
      seatNumber: booking.seat.seatNumber,
      carNumber: booking.seat.carNumber,
    };
  }

  const checkedInAt = new Date();
  await prisma.booking.update({
    where: { id: booking.id },
    data: { isCheckedIn: true, checkedInAt },
  });

  sseBroker.broadcastToTrip(booking.tripId, 'booking:update', {
    bookingId: booking.id,
    status: 'CHECKED_IN',
    seatNumber: booking.seat.seatNumber,
    message: `Check-in bestätigt: Wagen ${booking.seat.carNumber}, Platz ${booking.seat.seatNumber}.`,
  });
  sseBroker.sendToUser(booking.tripId, userId, 'booking:update', {
    bookingId: booking.id,
    status: 'CHECKED_IN',
    checkedInAt: checkedInAt.toISOString(),
  });

  return {
    bookingId: booking.id,
    checkedIn: true,
    checkedInAt,
    seatNumber: booking.seat.seatNumber,
    carNumber: booking.seat.carNumber,
  };
}

export interface NoShowSweepResult {
  scanned: number;
  released: string[];
}

/**
 * Barrido de No-Show: libera reservas CONFIRMED sin check-in cuya salida de
 * origen ya paso hace mas de NO_SHOW_GRACE_MINUTES (regla Comfort Check-In).
 * Se ejecuta periodicamente desde server.ts.
 */
export async function sweepNoShowBookings(): Promise<NoShowSweepResult> {
  const candidates = await prisma.booking.findMany({
    where: { status: 'CONFIRMED', isCheckedIn: false },
    include: { segment: true, seat: true, trip: { include: { serviceDates: true } } },
    take: 500,
  });

  const now = Date.now();
  const graceMs = env.NO_SHOW_GRACE_MINUTES * 60_000;
  const today = berlinDateStr();
  const released: string[] = [];

  for (const booking of candidates) {
    const time = booking.segment.departureTime;
    if (!time) continue;

    // Fecha de servicio mas reciente del viaje entre ayer y hoy (Berlin).
    const serviceDates = booking.trip.serviceDates
      .map((s) => berlinDateStr(s.serviceDate))
      .filter((d) => d <= today && d >= berlinDateStr(new Date(now - NO_SHOW_MAX_AGE_MS)))
      .sort();
    const serviceDate = serviceDates[serviceDates.length - 1];
    if (!serviceDate) continue;

    const departureMs = epochOfBerlinLocal(serviceDate, time);
    if (Number.isNaN(departureMs)) continue;

    const sinceDeparture = now - departureMs;
    // Solo dentro de la ventana (salida + gracia, salida + 6h): evita liberar
    // reservas de servicios antiguos o de fechas que aún no salen.
    if (sinceDeparture < graceMs || sinceDeparture > NO_SHOW_MAX_AGE_MS) continue;

    // Liberacion atomica: solo si sigue CONFIRMED y sin check-in (evita carreras
    // con un check-in concurrente o con el propio barrido).
    const updated = await prisma.booking.updateMany({
      where: { id: booking.id, status: 'CONFIRMED', isCheckedIn: false },
      data: {
        status: 'NO_SHOW',
        failureReason: `Kein Check-in nach Abfahrt (No-Show-Regel: ${env.NO_SHOW_GRACE_MINUTES} Min.)`,
        expiresAt: null,
      },
    });
    if (updated.count === 0) continue;

    await releaseResourceLock(`seat:${booking.seatId}:seg:${booking.segmentId}`, booking.id);

    sseBroker.broadcastToTrip(booking.tripId, 'seat:update', {
      seatId: booking.seatId,
      segmentId: booking.segmentId,
      status: 'AVAILABLE',
      bookingId: booking.id,
      seatNumber: booking.seat.seatNumber,
      segmentFrom: booking.segment.originStopName,
      segmentTo: booking.segment.destinationStopName,
      reason: 'no_show',
      at: new Date().toISOString(),
    });
    sseBroker.sendToUser(booking.tripId, booking.userId, 'booking:update', {
      bookingId: booking.id,
      status: 'NO_SHOW',
      message:
        `Kein Check-in nach Abfahrt in ${booking.segment.originStopName}. ` +
        'Der Sitzplatz wurde für folgende Teilstrecken freigegeben.',
    });

    released.push(booking.id);
  }

  return { scanned: candidates.length, released };
}
