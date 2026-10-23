/**
 * Numeros de tren reales (p. ej. "786") para cada viaje GTFS del dia.
 *
 * El feed GTFS agrupa por linea (route_short_name = "ICE 25"), igual que el
 * catalogo de la web. Este modulo cruza el tablero HAFAS de Fulda con la linea,
 * el terminus y la hora de paso planificada de cada viaje para descubrir el
 * numero de tren individual (el mismo que muestra DB Navigator) y guardarlo en
 * Redis para que resultados y detalle lo muestren como "ICE 25 · Tf 786".
 */

import { env } from '../../config/env';
import { prisma } from '../../shared/database/prisma';
import { redis } from '../../shared/redis/redis';
import { getBoard } from './realtime.service';

export interface TripTrainNumber {
  /** Numero de tren (p. ej. "786"). */
  number: string | null;
  /** Numero de linea HAFAS (p. ej. "25"). */
  lineNumber: string | null;
  /** Retraso de salida en Fulda (min). */
  delayMinutes: number | null;
  platform: string | null;
  terminus: string | null;
  /** Salida planificada en Fulda (ISO local, sin offset). */
  departure?: string;
}

const numOf = (name: string | null | undefined): string | null => {
  const m = String(name ?? '').match(/(\d{1,5})\s*$/);
  return m?.[1] ?? null;
};

const timeToMin = (t: string | null | undefined): number => {
  const [h, m] = String(t ?? '00:00').split(':').map((v) => Number.parseInt(v, 10) || 0);
  return h * 60 + m;
};

/** Ejecuta `fn` sobre `items` con maximo `limit` promesas simultaneas. */
async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next;
      next += 1;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return out;
}

const flatMinutes = (iso: string | undefined): number | null => {
  if (!iso) return null;
  const m = iso.match(/T(\d{2}):(\d{2})/);
  if (!m) return null;
  return Number.parseInt(m[1], 10) * 60 + Number.parseInt(m[2], 10);
};

interface Rows {
  trips: {
    id: string;
    routeName: string;
    destinationStop: string;
    originStop: string;
    fuldaDep: string | null;
  }[];
}

// Single-flight: evita que varias peticiones simultaneas disparen el barrido
// completo (que en Windows lanza PowerShell por cada hora del dia) a la vez.
const inflight = new Map<string, Promise<Record<string, TripTrainNumber>>>();

/**
 * Mapea numero de tren -> viaje GTFS para `date` (ISO yyyy-mm-dd).
 * Guarda en Redis (rt:nums:...) 15 min y devuelve { tripInternalId: info }.
 */
export function getTrainNumbers(date: string): Promise<Record<string, TripTrainNumber>> {
  const existing = inflight.get(date);
  if (existing) return existing;
  const pending = computeTrainNumbers(date).finally(() => inflight.delete(date));
  inflight.set(date, pending);
  return pending;
}

/**
 * Igual que `getTrainNumbers` pero nunca bloquea mas de `timeoutMs`: si el
 * barrido en vivo tarda mas, devuelve `{}` y el calculo continua en segundo
 * plano (single-flight) dejando el resultado en Redis para la siguiente vez.
 * Los llamadores de la busqueda/detalle usan esta version para que la web
 * cargue al instante aunque el tablero HAFAS tarde.
 */
export function getTrainNumbersFast(
  date: string,
  timeoutMs = 300,
): Promise<Record<string, TripTrainNumber>> {
  const empty = (): Record<string, TripTrainNumber> => ({});
  const compute = getTrainNumbers(date).catch(empty);
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        resolve(empty());
      }
    }, timeoutMs);
    void compute.then((value) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(value);
      }
    });
  });
}

async function computeTrainNumbers(date: string): Promise<Record<string, TripTrainNumber>> {
  const cacheKey = `rt:nums:${date}`;
  const cached = await redis.get(cacheKey).catch(() => null);
  if (cached) {
    try {
      const parsed = JSON.parse(cached) as Record<string, TripTrainNumber>;
      if (parsed && typeof parsed === 'object') return parsed;
    } catch {
      /* recalcula */
    }
  }

  const start = new Date(`${date}T00:00:00Z`);
  const end = new Date(`${date}T23:59:59Z`);
  // Solo viajes del día que pasan por Fulda: el tablero HAFAS consultado es de Fulda.
  const fuldaTrips = await prisma.$queryRaw<{ trip_id: string }[]>`
    SELECT DISTINCT tsd.trip_id
    FROM trip_service_dates tsd
    JOIN trip_stops ts ON ts.trip_id = tsd.trip_id
    WHERE tsd.service_date >= ${start}::date AND tsd.service_date <= ${end}::date
      AND ts.stop_name ILIKE '%fulda%'
  `;
  const tripIds = fuldaTrips.map((r) => r.trip_id);
  const result: Record<string, TripTrainNumber> = {};

  if (tripIds.length === 0) {
    await redis.set(cacheKey, '{}', 'EX', 900).catch(() => {});
    return result;
  }

  // Viajes del dia con su hora de paso por Fulda.
  const trips = await prisma.trip.findMany({
    where: { id: { in: tripIds } },
    select: {
      id: true,
      routeName: true,
      destinationStop: true,
      originStop: true,
      stops: {
        where: { stopName: { contains: 'fulda', mode: 'insensitive' } },
        orderBy: { stopSequence: 'asc' },
        select: { departureTime: true },
        take: 1,
      },
    },
  });
  const withFulda: Rows['trips'] = trips
    .filter((t) => (t.stops?.[0]?.departureTime ?? null) !== null)
    .map((t) => ({
      id: t.id,
      routeName: t.routeName,
      destinationStop: t.destinationStop,
      originStop: t.originStop,
      fuldaDep: t.stops?.[0]?.departureTime ?? null,
    }));

  if (withFulda.length === 0) {
    await redis.set(cacheKey, '{}', 'EX', 900).catch(() => {});
    return result;
  }

  // Ventana de horas a consultar en Fulda.
  const mins = withFulda.map((t) => timeToMin(t.fuldaDep));
  const first = Math.max(0, Math.min(...mins) - 20);
  const last = Math.min(24 * 60, Math.max(...mins) + 20);

  const boardPerHour = await mapLimit(
    Array.from({ length: Math.floor(last / 60) - Math.floor(first / 60) + 1 }, (_, i) => {
      const h = Math.floor(first / 60) + i;
      return `${date}T${String(h).padStart(2, '0')}:00`;
    }),
    4,
    (when) => getBoard(env.REALTIME_FULDA_EVA, when).catch(() => null),
  );
  const entries = boardPerHour.filter((b): b is import('./db-rest.provider').DbRestDeparture[] => b !== null).flat();

  for (const trip of withFulda) {
    const routeNumber = numOf(trip.routeName);
    const target = timeToMin(trip.fuldaDep);
    let best: { e: import('./db-rest.provider').DbRestDeparture; diff: number; score: number } | null = null;

    for (const dep of entries) {
      const lineNumber = dep.line?.lineNumber ?? numOf(dep.line?.name);
      if (routeNumber && lineNumber !== routeNumber) continue;
      const product = dep.line?.productShort;
      if (product && !trip.routeName.toUpperCase().startsWith(product)) continue;

      const depMinutes = flatMinutes(dep.plannedWhen);
      if (depMinutes === null) continue;
      const diff = Math.abs(depMinutes - target);
      if (diff > 30) continue;

      const dirNorm = (dep.direction ?? '').toLowerCase();
      const destNorm = trip.destinationStop.toLowerCase();
      const dirScore = dirNorm && (dirNorm.includes(destNorm) || destNorm.includes(dirNorm)) ? 0 : 3;

      const score = diff + dirScore;
      if (!best || score < best.score) {
        best = { e: dep, diff, score };
      }
    }

    if (!best) continue;
    const dep = best.e;
    const planned = dep.plannedWhen;
    const actual = dep.when;
    const delayMinutes =
      planned && actual && !Number.isNaN(Date.parse(planned)) && !Number.isNaN(Date.parse(actual))
        ? Math.round((Date.parse(actual) - Date.parse(planned)) / 60_000)
        : null;

    result[trip.id] = {
      number: numOf(dep.line?.name),
      lineNumber: dep.line?.lineNumber ?? null,
      delayMinutes,
      platform: dep.platform ?? null,
      terminus: dep.direction ?? null,
      departure: planned ?? undefined,
    };
  }

  await redis.set(cacheKey, JSON.stringify(result), 'EX', 900).catch(() => {});
  return result;
}