import type { FastifyInstance } from 'fastify';
import { authenticate, currentUser } from '../../shared/auth/middleware';
import { parseOrThrow } from '../../shared/validation';
import { cancelSchema, checkInSchema, checkoutSchema, holdSchema } from './bookings.schemas';
import {
  cancelBookingByUser,
  checkout,
  getBookingById,
  holdSeat,
  listUserBookings,
  sweepExpiredHolds,
} from './bookings.service';
import { checkInBooking, sweepNoShowBookings } from './checkin.service';

export async function bookingsRoutes(app: FastifyInstance): Promise<void> {
  app.addHook('preHandler', authenticate);

  app.post('/api/bookings/hold', async (request, reply) => {
    const input = parseOrThrow(holdSchema, request.body);
    const result = await holdSeat({ userId: currentUser(request).sub, ...input });
    return reply.code(201).send(result);
  });

  app.post('/api/bookings/checkout', async (request) => {
    const input = parseOrThrow(checkoutSchema, request.body);
    return checkout(currentUser(request).sub, input.bookingId, input.paymentMethodId);
  });

  app.post('/api/bookings/sweep', async () => {
    const released = await sweepExpiredHolds();
    return { released };
  });

  app.get('/api/bookings', async (request) => {
    return listUserBookings(currentUser(request).sub);
  });

  app.get('/api/bookings/:id', async (request) => {
    const { id } = request.params as { id: string };
    return getBookingById(currentUser(request).sub, id);
  });

  app.post('/api/bookings/:id/cancel', async (request) => {
    const { id } = request.params as { id: string };
    parseOrThrow(cancelSchema, { bookingId: id });
    return cancelBookingByUser(currentUser(request).sub, id);
  });

  /** Check-in digital (Comfort Check-In): valida la presencia del pasajero. */
  app.post('/api/bookings/:id/check-in', async (request) => {
    const { id } = request.params as { id: string };
    const body = (request.body ?? {}) as { qrPayload?: string };
    parseOrThrow(checkInSchema, { bookingId: id, qrPayload: body.qrPayload });
    return checkInBooking(currentUser(request).sub, id, body.qrPayload);
  });

  /** Barrido manual del No-Show (ademas del automatico de server.ts). */
  app.post('/api/bookings/sweep-no-show', async () => sweepNoShowBookings());
}