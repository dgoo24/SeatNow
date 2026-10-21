import { prisma } from '../../shared/database/prisma';
import { NotFoundError } from '../../shared/errors';
import { redis } from '../../shared/redis/redis';
import { getTrainNumbersFast, realtimeStatus, type TripTrainNumber } from '../realtime';
import { inventoryProvider } from '../inventory';

export interface SearchTripsInput {
  origin?: string;
  destination?: string;
  date?: string;
  limit?: number;
  timeType?: 'departure' | 'arrival';
  timeFrom?: string;
  timeTo?: string;
}

const timeToMinutes = (t: string): number => {
  const [h, m] = t.split(':').map((v) => Number.parseInt(v, 10) || 0);
  return h * 60 + m;
};

function dayRange(date: Date): { start: Date; end: Date } {
  // Limites UTC del dia calendario (evita mezclar dia anterior con DATE vs timestamptz).
  const y = date.getUTCFullYear();
  const m = String(date.getUTCMonth() + 1).padStart(2, '0');
  const d = String(date.getUTCDate()).padStart(2, '0');
  const ymd = `${y}-${m}-${d}`;
  return {
    start: new Date(`${ymd}T00:00:00.000Z`),
    end: new Date(`${ymd}T23:59:59.999Z`),
  };
}

/** Fecha actual en Europa/Berlin (yyyy-mm-dd). */
function berlinDate(date = new Date()): string {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Berlin',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  return fmt.format(date);
}

/** Búsqueda por TRAMO + fecha real de servicio (Teilstrecken en el día elegido). */
export async function searchTrips(input: SearchTripsInput) {
  const limit = Math.min(Math.max(input.limit ?? 20, 1), 50);

  if (input.date) {
    const d = new Date(input.date);
    if (!Number.isNaN(d.getTime())) {
      const { start, end } = dayRange(d);
      const activeDates = await prisma.tripServiceDate.findMany({
        where: { serviceDate: { gte: start, lte: end } },
        select: { tripId: true },
      });
      const tripIds = [...new Set(activeDates.map((x) => x.tripId))];
      if (tripIds.length === 0) return [];

      const segments = await prisma.segment.findMany({
        where: {
          tripId: { in: tripIds },
          ...(input.origin
            ? { originStopName: { contains: input.origin, mode: 'insensitive' } }
            : {}),
          ...(input.destination
            ? { destinationStopName: { contains: input.destination, mode: 'insensitive' } }
            : {}),
          ...(() => {
            const where: Record<string, unknown> = {};
            const tf = input.timeFrom ? input.timeFrom.replace(/:\d{2}$/, ':00') : undefined;
            const ttSrc = input.timeTo ? input.timeTo.replace(/:\d{2}$/, ':00') : undefined;
            const field = input.timeType === 'arrival' ? 'arrivalTime' : 'departureTime';
            if (input.timeFrom && input.timeTo) {
              const tt = input.timeTo.length >= 5 ? (input.timeTo.slice(0, 5) + ':59') : ttSrc;
              where[field] = { gte: tf, lte: tt };
            } else if (input.timeFrom) {
              where[field] = { gte: tf };
            } else if (input.timeTo) {
              const tt = input.timeTo.length >= 5 ? (input.timeTo.slice(0, 5) + ':00') : ttSrc;
              where[field] = { lte: tt };
            }
            return Object.keys(where).length ? where : {};
          })(),
        },
        include: { trip: true },
        orderBy: { departureTime: 'asc' },
      });

      // Numeros de tren reales (p. ej. "ICE 786") del tablero HAFAS de Fulda.
      // Best-effort con timeout: no debe bloquear la busqueda si HAFAS tarda.
      const numbers: Record<string, TripTrainNumber> = await getTrainNumbersFast(input.date);

      const byTrip = new Map<string, (typeof segments)[number]>();
      for (const s of segments) if (!byTrip.has(s.tripId)) byTrip.set(s.tripId, s);

      const rows = await Promise.all(
        [...byTrip.values()].slice(0, limit).map(async (segment) => {
          let availableSeats = 0;
          try {
            const availability = await inventoryProvider().getSegmentAvailability(segment.id);
            availableSeats = availability.filter((a) => a.status === 'AVAILABLE').length;
          } catch {
            availableSeats = 0;
          }
          const num = numbers[segment.trip.id];
          return {
            id: segment.trip.id,
            tripId: segment.trip.tripId,
            routeName: segment.trip.routeName,
            trainNumber: num?.number ?? null,
            originStop: segment.originStopName,
            destinationStop: num?.terminus ?? segment.destinationStopName,
            departureTime: segment.departureTime,
            arrivalTime: segment.arrivalTime,
            durationMinutes: segment.durationMinutes,
            totalSeats: 0,
            availableSeats,
            segmentId: segment.id,
          };
        }),
      );
      return rows.sort(
        (a, b) => timeToMinutes(a.departureTime) - timeToMinutes(b.departureTime),
      );
    }
  }

  // Fallback sin fecha: cualquier viaje (ignora TripServiceDate).
  const where: Record<string, unknown> = {};
  if (input.origin) where.originStop = { contains: input.origin, mode: 'insensitive' };
  if (input.destination) where.destinationStop = { contains: input.destination, mode: 'insensitive' };

  const trips = await prisma.trip.findMany({
    where,
    include: { _count: { select: { seats: true } } },
    orderBy: { departureTime: 'asc' },
    take: limit,
  });

  return trips.map((trip) => ({
    id: trip.id,
    tripId: trip.tripId,
    routeName: trip.routeName,
    originStop: trip.originStop,
    destinationStop: trip.destinationStop,
    departureTime: trip.departureTime,
    arrivalTime: trip.arrivalTime,
    durationMinutes: trip.durationMinutes,
    totalSeats: trip._count.seats,
    availableSeats: 0,
  }));
}

export async function getTripDetails(tripId: string) {
  const trip = await prisma.trip.findUnique({
    where: { id: tripId },
    include: {
      stops: { orderBy: { stopSequence: 'asc' } },
      segments: { orderBy: [{ originSequence: 'asc' }, { destinationSequence: 'asc' }] },
    },
  });
  if (!trip) throw new NotFoundError('Fahrt nicht gefunden');

  let num: TripTrainNumber | undefined;
  try {
    const d = berlinDate();
    num = (await getTrainNumbersFast(d))[trip.id];
  } catch {
    num = undefined;
  }

  return {
    id: trip.id,
    tripId: trip.tripId,
    routeName: trip.routeName,
    trainNumber: num?.number ?? null,
    trainDelayMinutes: num?.delayMinutes ?? null,
    originStop: trip.originStop,
    destinationStop: trip.destinationStop,
    departureTime: trip.departureTime,
    arrivalTime: trip.arrivalTime,
    durationMinutes: trip.durationMinutes,
    price: Number(trip.price),
    stops: trip.stops.map((s) => ({
      id: s.id,
      stopId: s.stopId,
      stopName: s.stopName,
      arrivalTime: s.arrivalTime,
      departureTime: s.departureTime,
      stopSequence: s.stopSequence,
    })),
    segments: trip.segments.map((s) => ({
      id: s.id,
      from: s.originStopName,
      to: s.destinationStopName,
      departureTime: s.departureTime,
      arrivalTime: s.arrivalTime,
      durationMinutes: s.durationMinutes,
      distanceKm: s.distanceKm,
      price: Number(s.price),
    })),
  };
}

export async function getTripSegments(tripId: string) {
  const trip = await prisma.trip.findUnique({
    where: { id: tripId },
    select: { id: true },
  });
  if (!trip) throw new NotFoundError('Fahrt nicht gefunden');
  const segments = await prisma.segment.findMany({
    where: { tripId },
    orderBy: [{ originSequence: 'asc' }, { destinationSequence: 'asc' }],
  });
  return segments.map((s) => ({
    id: s.id,
    from: s.originStopName,
    to: s.destinationStopName,
    departureTime: s.departureTime,
    arrivalTime: s.arrivalTime,
    durationMinutes: s.durationMinutes,
    distanceKm: s.distanceKm,
    price: Number(s.price),
  }));
}

export async function getTripSeats(tripId: string) {
  const trip = await prisma.trip.findUnique({ where: { id: tripId }, select: { id: true } });
  if (!trip) throw new NotFoundError('Fahrt nicht gefunden');

  const seats = await prisma.seat.findMany({
    where: { tripId },
    orderBy: [{ carNumber: 'asc' }, { seatNumber: 'asc' }],
  });
  return seats.map((s) => ({
    id: s.id,
    seatNumber: s.seatNumber,
    carNumber: s.carNumber,
  }));
}

/** Disponibilidad del mapa del vagón para un tramo concreto. */
export async function getSegmentAvailability(tripId: string, segmentId: string) {
  const trip = await prisma.trip.findUnique({ where: { id: tripId }, select: { id: true } });
  if (!trip) throw new NotFoundError('Fahrt nicht gefunden');
  const segment = await prisma.segment.findFirst({ where: { id: segmentId, tripId } });
  if (!segment) throw new NotFoundError('Teilstrecke für diese Fahrt nicht gefunden');
  return inventoryProvider().getSegmentAvailability(segment.id);
}

/** Autocompletar estaciones (solo nombres que existen en los tramos). */
export async function listStops(query: string, limit = 20) {
  const q = query.trim();
  if (q.length === 0) return [];
  const rows = await prisma.segment.findMany({
    where: {
      OR: [
        { originStopName: { contains: q, mode: 'insensitive' } },
        { destinationStopName: { contains: q, mode: 'insensitive' } },
      ],
    },
    select: { originStopName: true, destinationStopName: true },
    take: 500,
  });
  const needle = q.toLowerCase();
  const seen = new Set<string>();
  const unique: { stopId: string; stopName: string }[] = [];
  const add = (name: string) => {
    if (!name.toLowerCase().includes(needle)) return;
    const key = name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    unique.push({ stopId: key, stopName: name });
  };
  for (const r of rows) {
    add(r.originStopName);
    add(r.destinationStopName);
  }
  unique.sort((a, b) => a.stopName.localeCompare(b.stopName));
  return unique.slice(0, Math.min(Math.max(limit, 1), 50));
}

/** Distancia en km entre dos puntos (haversine). */
function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const toRad = (v: number) => (v * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Estación más cercana a una coordenada (para origen por GPS). */
export async function findNearestStop(lat: number, lon: number) {
  const stops = await prisma.stop.findMany({
    where: { lat: { not: null }, lon: { not: null } },
    select: { stopId: true, stopName: true, lat: true, lon: true },
  });
  if (stops.length === 0) return null;

  // Los nombres de "stops" y de los tramos pueden diferir (p. ej. "Fulda Bahnhof"
  // en stops vs "Fulda" en segments). Si elegimos uno que no existe en los tramos,
  // la búsqueda posterior devuelve 0 trenes. Por eso priorizamos estaciones que
  // sí aparecen como origen/destino de algún tramo.
  const usedRows = await prisma.$queryRaw<{ name: string }[]>`
    SELECT DISTINCT origin_stop_name AS name FROM segments
    UNION
    SELECT DISTINCT destination_stop_name AS name FROM segments
  `;
  const used = new Set(usedRows.map((r) => r.name));
  const usable = stops.filter((s) => used.has(s.stopName));
  const pool = usable.length > 0 ? usable : stops;

  let best: { stopId: string; stopName: string; lat: number | null; lon: number | null } | null = null;
  let bestKm = Number.POSITIVE_INFINITY;
  for (const s of pool) {
    if (s.lat == null || s.lon == null) continue;
    const km = haversineKm(lat, lon, s.lat, s.lon);
    if (km < bestKm) {
      bestKm = km;
      best = s;
    }
  }
  if (!best) return null;
  return {
    stopId: best.stopId,
    stopName: best.stopName,
    lat: best.lat,
    lon: best.lon,
    distanceKm: Number(bestKm.toFixed(3)),
  };
}

export async function getTrainStats() {
  const [trips, seats, stops, routes, segments, serviceDates, lastGtfsIngestedAt] =
    await Promise.all([
      prisma.trip.count(),
      prisma.seat.count(),
      prisma.stop.count(),
      prisma.route.count(),
      prisma.segment.count(),
      prisma.tripServiceDate.count(),
      redis.get('meta:gtfs:lastIngestedAt').catch(() => null),
    ]);
  return { trips, seats, stops, routes, segments, serviceDates, lastGtfsIngestedAt, realtime: realtimeStatus };
}