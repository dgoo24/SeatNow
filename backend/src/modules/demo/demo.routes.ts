import type { FastifyInstance } from 'fastify';
import { simulatePurchase, seedNoShowBooking } from './demo.service';
import { sweepNoShowBookings } from '../bookings/checkin.service';
import { handleTripDisruption } from '../bookings/disruption.service';

/** Rutas de DEMO para desarrollar la interfaz en vivo (solo se registran en dev). */
export async function demoRoutes(app: FastifyInstance): Promise<void> {
  app.post('/api/demo/purchase', async (request, reply) => {
    const body = (request.body || {}) as { tripId?: string; segmentId?: string };
    const result = await simulatePurchase(body);
    return reply.code(201).send(result);
  });

  /**
   * No-Show (Comfort Check-In): crea una reserva CONFIRMED sin check-in en un
   * tramo que ya salió hoy (hace 16-300 min). Luego llama a /sweep para liberarla.
   */
  app.post('/api/demo/no-show/seed', async (request, reply) => {
    const result = await seedNoShowBooking();
    return reply.code(201).send({
      ...result,
      hint: 'Ahora ejecuta POST /api/bookings/sweep-no-show (o espera el barrido automatico) y veras el asiento liberado via SSE.',
    });
  });

  /** Ejecuta el barrido No-Show manualmente (el automatico corre en server.ts). */
  app.post('/api/demo/no-show/sweep', async () => sweepNoShowBookings());

  /**
   * Simula la deteccion realtime de la cancelacion de un tren para un tripId:
   * fuerza handleTripDisruption({ cancelled: true }) sin esperar al poller.
   * Uso: primero POST /api/demo/purchase para tener reservas en el viaje.
   */
  app.post('/api/demo/disruption/cancel', async (request, reply) => {
    const body = (request.body || {}) as { tripId?: string };
    if (!body.tripId) {
      return reply.code(400).send({ error: 'tripId requerido' });
    }
    const result = await handleTripDisruption(body.tripId, { cancelled: true }, { force: true });
    return reply.code(200).send(result);
  });
}
