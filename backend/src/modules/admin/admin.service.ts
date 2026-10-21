import { prisma } from '../../shared/database/prisma';
import { NotFoundError } from '../../shared/errors';
import { toPublicBooking, type PublicBooking } from '../bookings/bookings.service';

export interface AdminBooking extends PublicBooking {
  user?: { id: string; email: string; fullName: string };
}

const bookingInclude = { trip: true, seat: true, segment: true, user: true } as const;

function toAdminBooking(booking: {
  trip: unknown;
  seat: unknown;
  segment: unknown;
  user: { id: string; email: string; fullName: string } | null;
  [k: string]: unknown;
}): AdminBooking {
  const publicBooking = toPublicBooking({ ...booking } as never);
  return {
    ...publicBooking,
    user: booking.user ? { id: booking.user.id, email: booking.user.email, fullName: booking.user.fullName } : undefined,
  };
}

/** Reservas en curso (retenidas, a la espera del pago). */
export async function listPendingBookings(): Promise<AdminBooking[]> {
  const bookings = await prisma.booking.findMany({
    where: { status: 'PENDING_PAYMENT' },
    include: bookingInclude,
    orderBy: { expiresAt: 'asc' },
  });
  return bookings.map((b) => toAdminBooking({ ...b } as never));
}

export async function listAllBookings(): Promise<AdminBooking[]> {
  const bookings = await prisma.booking.findMany({
    include: bookingInclude,
    orderBy: { createdAt: 'desc' },
    take: 200,
  });
  return bookings.map((b) => toAdminBooking({ ...b } as never));
}

export async function getAdminBooking(bookingId: string): Promise<AdminBooking> {
  const booking = await prisma.booking.findUnique({
    where: { id: bookingId },
    include: bookingInclude,
  });
  if (!booking) throw new NotFoundError('Buchung nicht gefunden');
  return toAdminBooking({ ...booking } as never);
}