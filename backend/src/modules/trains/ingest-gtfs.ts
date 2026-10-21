import { env } from '../../config/env';
import { prisma, disconnectDatabase } from '../../shared/database/prisma';
import { disconnectRedis, redis } from '../../shared/redis/redis';
import {
  expandServiceDates,
  haversineKm,
  loadGtfsDataset,
  type GtfsStopTime,
} from './gtfs-parser';

const CAR_COUNT = 2;
const SEATS_PER_CAR = 25;
const CHUNK = 5000;

const timeToMinutes = (t: string): number => {
  const [h, m] = t.split(':').map((v) => Number.parseInt(v, 10) || 0);
  return h * 60 + m;
};

function diffMinutes(a: string, b: string): number {
  let d = timeToMinutes(b) - timeToMinutes(a);
  if (d <= 0) d += 24 * 60;
  return d;
}

/** Precio estimado del billete completo (referencia). */
function computeTripPrice(durationMinutes: number, stopCount: number): number {
  const base = 19.9;
  const perStop = Math.max(0, stopCount - 1) * 4.5;
  const perHour = (durationMinutes / 60) * 6;
  const raw = base + perStop + perHour;
  return Math.round(raw * 100) / 100;
}

/**
 * Precio estimado de una reserva de asiento por tramo (~5.20 EUR como DB).
 * Base minima 4.90, crece ligeramente con la distancia, tope 5.90.
 */
function computeSegmentPrice(distanceKm: number): number {
  const raw = 3.4 + distanceKm * 0.005;
  const clamped = Math.min(5.9, Math.max(4.9, raw));
  return Math.round(clamped * 100) / 100;
}

async function chunkedCreateMany(
  data: { [k: string]: unknown }[],
  build: (chunk: { [k: string]: unknown }[]) => Promise<unknown>,
): Promise<number> {
  let created = 0;
  for (let i = 0; i < data.length; i += CHUNK) {
    const chunk = data.slice(i, i + CHUNK);
    const res = (await build(chunk)) as { count: number };
    created += res.count;
  }
  return created;
}

export async function ingestGtfs(source?: string): Promise<{
  routes: number;
  stops: number;
  trips: number;
  seats: number;
  serviceDates: number;
  segments: number;
  tripStops: number;
  filteredOut: number;
  effectiveSource: string;
}> {
  const effectiveSource = source || env.GTFS_URL || env.GTFS_SOURCE;
  // eslint-disable-next-line no-console
  console.log(`[gtfs] Cargando dataset desde: ${effectiveSource}`);
  const dataset = await loadGtfsDataset(effectiveSource);

  // --- Filtro por paradas objetivo (p. ej. "Fulda") ---
  const filterTokens = (env.GTFS_FILTER_STOPS ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  const keptStopIds = new Set<string>();
  if (filterTokens.length > 0) {
    for (const stop of dataset.stops) {
      const name = stop.stop_name.toLowerCase();
      if (filterTokens.some((t) => name.includes(t))) keptStopIds.add(stop.stop_id);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `[gtfs] Filtro de paradas: ${filterTokens.join(', ') || 'ninguno'} -> ${keptStopIds.size} paradas`,
  );

  const stopNameById = new Map(dataset.stops.map((s) => [s.stop_id, s.stop_name] as const));
  const stopCoordById = new Map(dataset.stops.map((s) => [s.stop_id, s] as const));
  const routeById = new Map(dataset.routes.map((r) => [r.route_id, r] as const));

  // --- Routes ---
  for (const route of dataset.routes) {
    await prisma.route.upsert({
      where: { routeId: route.route_id },
      create: {
        routeId: route.route_id,
        routeName: route.route_long_name || route.route_short_name || route.route_id,
        routeType: route.route_type ?? null,
      },
      update: {
        routeName: route.route_long_name || route.route_short_name || route.route_id,
        routeType: route.route_type ?? null,
      },
    });
  }

  // --- Stops ---
  for (const stop of dataset.stops) {
    await prisma.stop.upsert({
      where: { stopId: stop.stop_id },
      create: {
        stopId: stop.stop_id,
        stopName: stop.stop_name,
        lat: stop.stop_lat ?? null,
        lon: stop.stop_lon ?? null,
      },
      update: { stopName: stop.stop_name, lat: stop.stop_lat ?? null, lon: stop.stop_lon ?? null },
    });
  }

  // --- Agrupar stop_times por trip ---
  const timesByTrip = new Map<string, GtfsStopTime[]>();
  for (const st of dataset.stopTimes) {
    const list = timesByTrip.get(st.trip_id) ?? [];
    list.push(st);
    timesByTrip.set(st.trip_id, list);
  }

  // --- Seleccion de trips (>=2 paradas y, si hay filtro, que pase por una parada objetivo) ---
  const keptTrips = dataset.trips.filter((trip) => {
    const times = timesByTrip.get(trip.trip_id) ?? [];
    if (times.length < 2) return false;
    if (keptStopIds.size === 0) return true;
    return times.some((t) => keptStopIds.has(t.stop_id));
  });

  let seatsCreated = 0;
  let serviceDatesCreated = 0;
  let segmentsCreated = 0;
  let tripStopsCreated = 0;

  const seatBatch: { tripId: string; seatNumber: string; carNumber: number }[] = [];
  const serviceDateBatch: { tripId: string; serviceDate: Date }[] = [];
  const tripStopBatch: {
    tripId: string;
    stopId: string;
    stopName: string;
    arrivalTime: string | null;
    departureTime: string | null;
    stopSequence: number;
  }[] = [];
  const segmentBatch: {
    tripId: string;
    originStopName: string;
    destinationStopName: string;
    originSequence: number;
    destinationSequence: number;
    departureTime: string;
    arrivalTime: string;
    durationMinutes: number | null;
    distanceKm: number;
    price: number;
  }[] = [];

  for (const trip of keptTrips) {
    const times = (timesByTrip.get(trip.trip_id) ?? []).sort(
      (a, b) => a.stop_sequence - b.stop_sequence,
    );
    if (times.length < 2) continue;

    const first = times[0];
    const last = times[times.length - 1];
    const route = routeById.get(trip.route_id);
    const routeName = route?.route_long_name || route?.route_short_name || trip.route_id;

    const departure = first.departure_time;
    const arrival = last.arrival_time;
    const duration = diffMinutes(departure, arrival);

    const upserted = await prisma.trip.upsert({
      where: { tripId: trip.trip_id },
      create: {
        tripId: trip.trip_id,
        routeId: trip.route_id,
        routeName,
        originStop: stopNameById.get(first.stop_id) ?? first.stop_id,
        destinationStop: stopNameById.get(last.stop_id) ?? last.stop_id,
        departureTime: departure,
        arrivalTime: arrival,
        durationMinutes: duration,
        price: computeTripPrice(duration, times.length),
        serviceDate: null,
      },
      update: {
        routeId: trip.route_id,
        routeName,
        originStop: stopNameById.get(first.stop_id) ?? first.stop_id,
        destinationStop: stopNameById.get(last.stop_id) ?? last.stop_id,
        departureTime: departure,
        arrivalTime: arrival,
        durationMinutes: duration,
        price: computeTripPrice(duration, times.length),
      },
      select: { id: true, routeId: true, routeName: true },
    });

    // Paradas ordenadas
    for (const st of times) {
      tripStopBatch.push({
        tripId: upserted.id,
        stopId: st.stop_id,
        stopName: stopNameById.get(st.stop_id) ?? st.stop_id,
        arrivalTime: st.arrival_time ? st.arrival_time : null,
        departureTime: st.departure_time ? st.departure_time : null,
        stopSequence: st.stop_sequence,
      });
    }

    // Fechas de servicio
    for (const iso of expandServiceDates(
      trip.service_id,
      dataset.calendars,
      dataset.calendarExceptions,
    )) {
      serviceDateBatch.push({ tripId: upserted.id, serviceDate: new Date(`${iso}T00:00:00Z`) });
    }

    // Segmentos: todos los pares (i, j) con j > i
    for (let i = 0; i < times.length; i += 1) {
      for (let j = i + 1; j < times.length; j += 1) {
        const o = times[i];
        const d = times[j];
        const originTime = o.departure_time || o.arrival_time;
        const destTime = d.arrival_time || d.departure_time;
        const segDuration = diffMinutes(originTime, destTime);
        const distanceKm = haversineKm(
          stopCoordById.get(o.stop_id) ?? {},
          stopCoordById.get(d.stop_id) ?? {},
        );
        segmentBatch.push({
          tripId: upserted.id,
          originStopName: stopNameById.get(o.stop_id) ?? o.stop_id,
          destinationStopName: stopNameById.get(d.stop_id) ?? d.stop_id,
          originSequence: o.stop_sequence,
          destinationSequence: d.stop_sequence,
          departureTime: originTime,
          arrivalTime: destTime,
          durationMinutes: segDuration,
          distanceKm,
          price: computeSegmentPrice(distanceKm),
        });
      }
    }

    // Asientos
    for (let car = 1; car <= CAR_COUNT; car += 1) {
      for (let seat = 1; seat <= SEATS_PER_CAR; seat += 1) {
        seatBatch.push({ tripId: upserted.id, carNumber: car, seatNumber: String(seat).padStart(2, '0') });
      }
    }
  }

  // --- Inserciones en lotes ---
  seatsCreated += await chunkedCreateMany(seatBatch, (chunk) =>
    prisma.seat.createMany({
      data: chunk as { tripId: string; seatNumber: string; carNumber: number }[],
      skipDuplicates: true,
    }),
  );
  tripStopsCreated += await chunkedCreateMany(tripStopBatch, (chunk) =>
    prisma.tripStop.createMany({
      data: chunk as {
        tripId: string;
        stopId: string;
        stopName: string;
        arrivalTime: string | null;
        departureTime: string | null;
        stopSequence: number;
      }[],
      skipDuplicates: true,
    }),
  );
  serviceDatesCreated += await chunkedCreateMany(serviceDateBatch, (chunk) =>
    prisma.tripServiceDate.createMany({
      data: chunk as { tripId: string; serviceDate: Date }[],
      skipDuplicates: true,
    }),
  );
  segmentsCreated += await chunkedCreateMany(segmentBatch, (chunk) =>
    prisma.segment.createMany({
      data: chunk as {
        tripId: string;
        originStopName: string;
        destinationStopName: string;
        originSequence: number;
        destinationSequence: number;
        departureTime: string;
        arrivalTime: string;
        durationMinutes: number | null;
        distanceKm: number;
        price: number;
      }[],
      skipDuplicates: true,
    }),
  );

  // eslint-disable-next-line no-console
  console.log(`[gtfs] Saltados ${keptTrips.length} -> son los que se conservaron`);

  return {
    routes: dataset.routes.length,
    stops: dataset.stops.length,
    trips: keptTrips.length,
    seats: seatsCreated,
    serviceDates: serviceDatesCreated,
    segments: segmentsCreated,
    tripStops: tripStopsCreated,
    filteredOut: dataset.trips.length - keptTrips.length,
    effectiveSource,
  };
}

async function main(): Promise<void> {
  const argSource = process.argv[2];
  const result = await ingestGtfs(argSource);
  // eslint-disable-next-line no-console
  console.log(
    `[gtfs] Ingesta completa -> routes: ${result.routes}, stops: ${result.stops}, trips: ${result.trips}, ` +
      `tripStops: ${result.tripStops}, serviceDates: ${result.serviceDates}, segments: ${result.segments}, ` +
      `seats nuevos: ${result.seats} (descartados por filtro: ${result.filteredOut})`,
  );

  // Marca la ultima ingesta para la cabecera de la web (/stats).
  try {
    await redis.set('meta:gtfs:lastIngestedAt', new Date().toISOString());
    await redis.set('meta:gtfs:lastSource', result.effectiveSource);
    await disconnectRedis();
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[gtfs] No se pudo guardar la marca de ingesta en Redis:', (err as Error).message);
  }

  await disconnectDatabase();
  process.exit(0);
}

if (require.main === module) {
  main().catch(async (err) => {
    // eslint-disable-next-line no-console
    console.error('[gtfs] Error en la ingesta:', err);
    await disconnectDatabase();
    process.exit(1);
  });
}