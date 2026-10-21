import type { Booking } from '@prisma/client';
import { prisma } from '../../shared/database/prisma';
import { ConflictError, NotFoundError } from '../../shared/errors';
import { sseBroker } from '../../shared/sse/sse-broker';
import { generateBoardingPassPdf } from '../../shared/pdf/ticket';
import { bookingConfirmedHtml, sendMail } from '../../shared/email/mailer';
import { captureIntent, createManualCaptureIntent, paymentsMode } from '../payments/payments.service';
import { inventoryProvider } from '../inventory';

const bookingInclude = {
  trip: true,
  seat: true,
  segment: true,
} as const;

export interface PublicBooking {
  id: string;
  status: string;
  totalAmount: number;
  qrPayload: string | null;
  failureReason: string | null;
  expiresAt: Date | null;
  isCheckedIn: boolean;
  checkedInAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
  trip: {
    id: string;
    tripId: string;
    routeName: string;
    originStop: string;
    destinationStop: string;
    departureTime: string;
    arrivalTime: string;
  };
  seat: { id: string; seatNumber: string; carNumber: number };
  segment: {
    id: string;
    from: string;
    to: string;
    departureTime: string;
    arrivalTime: string;
    durationMinutes: number | null;
    distanceKm: number;
    price: number;
  };
}

export function toPublicBooking(booking: Booking & {
  trip: Record<string, unknown>;
  seat: Record<string, unknown>;
  segment: Record<string, unknown>;
}): PublicBooking {
  const trip = booking.trip as { id: string; tripId: string; routeName: string; originStop: string; destinationStop: string; departureTime: string; arrivalTime: string };
  const seat = booking.seat as { id: string; seatNumber: string; carNumber: number };
  const segment = booking.segment as { id: string; originStopName: string; destinationStopName: string; departureTime: string; arrivalTime: string; durationMinutes: number | null; distanceKm: number; price: { toNumber: () => number } };
  return {
    id: booking.id,
    status: booking.status,
    totalAmount: Number(booking.totalAmount),
    qrPayload: booking.qrPayload,
    failureReason: booking.failureReason,
    expiresAt: booking.expiresAt,
    isCheckedIn: booking.isCheckedIn,
    checkedInAt: booking.checkedInAt,
    createdAt: booking.createdAt,
    updatedAt: booking.updatedAt,
    trip: {
      id: trip.id,
      tripId: trip.tripId,
      routeName: trip.routeName,
      originStop: trip.originStop,
      destinationStop: trip.destinationStop,
      departureTime: trip.departureTime,
      arrivalTime: trip.arrivalTime,
    },
    seat: { id: seat.id, seatNumber: seat.seatNumber, carNumber: seat.carNumber },
    segment: {
      id: segment.id,
      from: segment.originStopName,
      to: segment.destinationStopName,
      departureTime: segment.departureTime,
      arrivalTime: segment.arrivalTime,
      durationMinutes: segment.durationMinutes,
      distanceKm: segment.distanceKm,
      price: Number(segment.price),
    },
  };
}

export interface HoldSeatInput {
  userId: string;
  tripId: string;
  seatId: string;
  segmentId: string;
}

export async function holdSeat(input: HoldSeatInput) {
  const result = await inventoryProvider().holdSeat(input);
  return {
    booking: toPublicBooking(result.booking as never),
    ttlSeconds: result.ttlSeconds,
    expiresAt: result.expiresAt,
  };
}

/** Confirmacion INSTANTANEA: se captura el pago mock y se confirma sin operadora. */
export async function checkout(userId: string, bookingId: string, paymentMethodId?: string) {
  const booking = await prisma.booking.findUnique({ where: { id: bookingId }, include: bookingInclude });
  if (!booking) throw new NotFoundError('Buchung nicht gefunden');
  if (booking.userId !== userId) throw new NotFoundError('Buchung nicht gefunden');
  if (booking.status !== 'PENDING_PAYMENT') {
    throw new ConflictError(`Die Buchung wurde bereits verarbeitet (Status ${booking.status})`);
  }

  const intent = await createManualCaptureIntent({
    amount: Number(booking.totalAmount),
    description: `Sitzplatzbuchung ${booking.seat.seatNumber} ${booking.segment.originStopName} -> ${booking.segment.destinationStopName}`,
    metadata: { bookingId: booking.id, userId },
    paymentMethodId,
  });
  const capture = await captureIntent(intent.id);
  if (capture.status !== 'succeeded') {
    throw new ConflictError(`Die Zahlung konnte nicht eingezogen werden (Status ${capture.status})`);
  }

  await prisma.booking.update({
    where: { id: booking.id },
    data: { stripePaymentIntentId: intent.id },
  });

  await inventoryProvider().confirmBooking(booking.id);

  // Generar QR + PDF + notificar (best-effort si SMTP no esta configurado).
  const qrPayload = `TKT-${booking.id.replace(/-/g, '').slice(0, 16).toUpperCase()}`;
  await prisma.booking.update({ where: { id: booking.id }, data: { qrPayload } });

  const confirmed = await prisma.booking.findUnique({
    where: { id: booking.id },
    include: bookingInclude,
  });
  if (!confirmed) throw new NotFoundError('Buchung nicht gefunden');

  const user = await prisma.user.findUnique({ where: { id: userId }, select: { email: true, fullName: true } });

  const boardingPass = await generateBoardingPassPdf({
    bookingId: booking.id,
    fullName: user?.fullName ?? 'Fahrgast',
    origin: booking.segment.originStopName,
    destination: booking.segment.destinationStopName,
    departureTime: booking.segment.departureTime,
    arrivalTime: booking.segment.arrivalTime,
    seatNumber: booking.seat.seatNumber,
    carNumber: booking.seat.carNumber,
    qrPayload,
  });

  const emailSent = await sendMail({
    to: user?.email ?? '',
    subject: `Ticket bestätigt: ${booking.segment.originStopName} -> ${booking.segment.destinationStopName}`,
    html: bookingConfirmedHtml({
      fullName: user?.fullName ?? 'Fahrgast',
      origin: booking.segment.originStopName,
      destination: booking.segment.destinationStopName,
      departureTime: booking.segment.departureTime,
      seatNumber: booking.seat.seatNumber,
      carNumber: booking.seat.carNumber,
      qrPayload,
    }),
    attachments: [{ filename: `ticket-${booking.id}.pdf`, content: boardingPass, contentType: 'application/pdf' }],
  });

  sseBroker.broadcastToTrip(booking.tripId, 'booking:update', {
    bookingId: booking.id,
    status: 'CONFIRMED',
    qrPayload,
    message: 'Dein Sitzplatz ist bestätigt. Zahlung erfolgt.',
  });
  sseBroker.sendToUser(booking.tripId, userId, 'booking:update', {
    bookingId: booking.id,
    status: 'CONFIRMED',
    qrPayload,
  });

  return { booking: toPublicBooking(confirmed as never), emailSent, payment: paymentsMode() };
}

export async function cancelBookingByUser(userId: string, bookingId: string) {
  const booking = await prisma.booking.findUnique({ where: { id: bookingId }, include: bookingInclude });
  if (!booking) throw new NotFoundError('Buchung nicht gefunden');
  if (booking.userId !== userId) throw new NotFoundError('Buchung nicht gefunden');

  const updated = await inventoryProvider().cancelBooking(booking.id);
  const reloaded = await prisma.booking.findUnique({ where: { id: booking.id }, include: bookingInclude });
  if (!reloaded) throw new NotFoundError('Buchung nicht gefunden');

  sseBroker.sendToUser(booking.tripId, userId, 'booking:update', {
    bookingId: booking.id,
    status: 'CANCELLED',
  });

  return toPublicBooking(reloaded as never);
}

export async function listUserBookings(userId: string) {
  const bookings = await prisma.booking.findMany({
    where: { userId },
    include: bookingInclude,
    orderBy: { createdAt: 'desc' },
    take: 100,
  });
  return bookings.map((b) => toPublicBooking(b as never));
}

export async function getBookingById(userId: string, bookingId: string) {
  const booking = await prisma.booking.findUnique({ where: { id: bookingId }, include: bookingInclude });
  if (!booking) throw new NotFoundError('Buchung nicht gefunden');
  if (booking.userId !== userId) throw new NotFoundError('Buchung nicht gefunden');
  return toPublicBooking(booking as never);
}

/** Tarea de fondo: libera retenciones expiradas y sus locks (por tramo). */
export async function sweepExpiredHolds(): Promise<number> {
  const provider = inventoryProvider();
  if (provider.name !== 'simulated') return 0;
  return provider.releaseExpired();
}

export { bookingInclude };