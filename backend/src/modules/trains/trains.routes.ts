import { randomUUID } from 'node:crypto';
import type { FastifyInstance } from 'fastify';
import { getLiveForTrip, getTrainNumbers, getTrainNumbersFast } from '../realtime';
import { sseBroker } from '../../shared/sse/sse-broker';
import {
  findNearestStop,
  getSegmentAvailability,
  getTrainStats,
  getTripDetails,
  getTripSeats,
  getTripSegments,
  listStops,
  searchTrips,
} from './trains.service';

interface SearchQuery {
  origin?: string;
  destination?: string;
  date?: string;
  limit?: string;
  timeType?: 'departure' | 'arrival';
  timeFrom?: string;
  timeTo?: string;
}

export async function trainsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/api/trains/search', async (request) => {
    const q = request.query as SearchQuery;
    return searchTrips({
      origin: q.origin,
      destination: q.destination,
      date: q.date,
      limit: q.limit ? Number.parseInt(q.limit, 10) : undefined,
      timeType: q.timeType,
      timeFrom: q.timeFrom,
      timeTo: q.timeTo,
    });
  });

  app.get('/api/trains/stops', async (request) => {
    const { q, limit } = request.query as { q?: string; limit?: string };
    return listStops(q ?? '', Number.parseInt(limit ?? '', 10) || 20);
  });

  /** Estación más cercana a una coordenada (origen por GPS). */
  app.get('/api/trains/stops/nearby', async (request, reply) => {
    const { lat, lon } = request.query as { lat?: string; lon?: string };
    const la = Number.parseFloat(lat ?? '');
    const lo = Number.parseFloat(lon ?? '');
    if (Number.isNaN(la) || Number.isNaN(lo)) {
      return reply.code(400).send({ message: 'lat und lon sind erforderlich' });
    }
    const stop = await findNearestStop(la, lo);
    if (!stop) return reply.code(404).send({ message: 'Keine Bahnhöfe mit Koordinaten' });
    return stop;
  });

  app.get('/api/trains/stats', async () => getTrainStats());

  app.get('/api/trains/:tripId', async (request) => {
    const { tripId } = request.params as { tripId: string };
    const { date } = request.query as { date?: string };
    const details = await getTripDetails(tripId);
    // Adjuntar numero del dia buscado si se pide; se usa en frontend
    if (date) {
      try {
        const nums = await getTrainNumbersFast(date);
        (details as any).trainNumberForDate = nums[tripId]?.number ?? null;
        (details as any).trainProductForDate = nums[tripId]?.lineNumber ?? null;
      } catch {
        // ignorar
      }
    }
    return details;
  });

  app.get('/api/trains/:tripId/segments', async (request) => {
    const { tripId } = request.params as { tripId: string };
    return getTripSegments(tripId);
  });

  app.get('/api/trains/:tripId/seats', async (request) => {
    const { tripId } = request.params as { tripId: string };
    return getTripSeats(tripId);
  });

  /** Mapa del vagón por tramo: para cada asiento su estado en ese tramo. */
  app.get('/api/trains/:tripId/segments/:segmentId/availability', async (request) => {
    const { tripId, segmentId } = request.params as { tripId: string; segmentId: string };
    return getSegmentAvailability(tripId, segmentId);
  });

  /** Mapa numero de tren -> viaje del dia (para depuracion / catalogo). */
  app.get('/api/trains/numbers', async (request) => {
    const { date } = request.query as { date?: string };
    const d = date ?? new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Berlin',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());
    return getTrainNumbers(d);
  });

  /** Estado en tiempo real (retraso, via, cancelacion, proximas paradas, posicion). */
  app.get('/api/trains/:tripId/realtime', async (request) => {
    const { tripId } = request.params as { tripId: string };
    const { date } = request.query as { date?: string };
    return getLiveForTrip(tripId, date);
  });

  // --- Canal en tiempo real (SSE) del mapa de asientos por tramo ---
  app.get('/api/trains/:tripId/live', async (request, reply) => {
    const { tripId } = request.params as { tripId: string };
    const { userId } = request.query as { userId?: string };

    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'Access-Control-Allow-Origin': '*',
      'X-Accel-Buffering': 'no',
    });
    res.write(': connected\n\n');

    const clientId = randomUUID();
    sseBroker.subscribe({ id: clientId, tripId, userId, res, createdAt: Date.now() });

    const seats = await getTripSeats(tripId).catch(() => []);
    const segments = await getTripSegments(tripId).catch(() => []);
    sseBroker.sendToClient(clientId, 'hello', {
      tripId,
      clientId,
      serverTime: new Date().toISOString(),
      seats,
      segments,
    });

    const heartbeat = setInterval(() => {
      try {
        res.write(`event: server:time\ndata: ${JSON.stringify({ at: new Date().toISOString() })}\n\n`);
      } catch {
        clearInterval(heartbeat);
        sseBroker.unsubscribe(clientId);
      }
    }, 15000);

    request.raw.on('close', () => {
      clearInterval(heartbeat);
      sseBroker.unsubscribe(clientId);
    });
  });
}