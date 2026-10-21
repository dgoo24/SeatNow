import type { FastifyInstance } from 'fastify';
import { requireAdmin } from '../../shared/auth/middleware';
import { getAdminBooking, listAllBookings, listPendingBookings } from './admin.service';

export async function adminRoutes(app: FastifyInstance): Promise<void> {
  app.addHook('preHandler', requireAdmin);

  app.get('/api/admin/pending-bookings', async () => listPendingBookings());

  app.get('/api/admin/bookings', async () => listAllBookings());

  app.get('/api/admin/bookings/:id', async (request) => {
    const { id } = request.params as { id: string };
    return getAdminBooking(id);
  });
}