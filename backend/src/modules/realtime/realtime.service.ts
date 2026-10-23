import { env } from '../../config/env';
import { prisma } from '../../shared/database/prisma';
import { redis } from '../../shared/redis/redis';
import { sseBroker } from '../../shared/sse/sse-broker';
import { addDaysIso, epochOfBerlinLocal } from '../../shared/time/berlin';
import { handleTripDisruption, shouldHandleRealtime } from '../bookings/disruption.service';
import { BahnWebProvider } from './bahn-web.provider';
import { DbRestProvider, type DbRestDeparture, type RealtimeProvider } from './db-rest.provider';
import { isGenericStationName, resolveStationEva } from './station-resolver';

export interface LiveNextStop {
  name: string | null;
  planned: string | null;
  actual: string | null;
  delayMinutes: number | null;
  cancelled: boolean;
  platform: string | null;
}

export interface LiveRealtimeDto {
  available: boolean;
  provider: string;
  reason?:
    | 'disabled'
    | 'not-running-today'
    | 'not-today'
    | 'finished'
    | 'no-match'
    | 'no-station'
    | 'upstream';
  message?: string;
  fetchedAt?: string;
  train?: {
    id: string;
    routeName: string;
    originStop: string;
    destinationStop: string;
    plannedDeparture: string;
    date: string;
  };
  live?: {
    eva: string;
    station: string;
    line: string | null;
    /** Numero de tren (p. ej. "786" en "ICE 786"). */
    number: string | null;
    direction: string | null;
    origin: string | null;
    delayMinutes: number | null;
    cancelled: boolean;
    plannedDeparture: string | null;
    actualDeparture: string | null;
    platform: string | null;
    plannedPlatform: string | null;
    position:
      | { kind: 'latlon'; latitude: number; longitude: number }
      | { kind: 'stop'; name: string | null }
      | null;
    nextStops: LiveNextStop[];
  };
}

const latLngOf = (raw: object | null | undefined) =>
  raw &&
  'latitude' in raw &&
  'longitude' in raw &&
  typeof raw.latitude === 'number' &&
  typeof raw.longitude === 'number'
    ? { kind: 'latlon' as const, latitude: raw.latitude, longitude: raw.longitude }
    : 'name' in (raw ?? {})
      ? { kind: 'stop' as const, name: (raw as { name?: string }).name ?? null }
      : null;

let providers: RealtimeProvider[] | null = null;

/** TTL del tablero en cache (< min(7), relacionado con la cadencia del poller en vivo). */
const RT_BOARD_TTL = Math.max(15, Math.min(60, Math.round(env.REALTIME_POLL_MS / 1000)));

export const realtimeStatus = {
  provider: env.REALTIME_PROVIDER === 'off' ? 'off' : env.REALTIME_PROVIDER,
  enabled: env.REALTIME_PROVIDER !== 'off',
  lastOk: null as string | null,
  lastError: null as string | null,
  lastFetchedAt: null as string | null,
};

function realtimeProviders(): RealtimeProvider[] {
  if (!providers) {
    providers = [];
    if (env.REALTIME_PROVIDER === 'bahn-web' || env.REALTIME_PROVIDER === 'db-rest') {
      if (env.REALTIME_PROVIDER === 'bahn-web') {
        providers.push(new BahnWebProvider());
      }
      providers.push(new DbRestProvider());
    }
  }
  return providers;
}

function dayRange(date: Date): { start: Date; end: Date } {
  // El filtro de serviceDate debe apuntar a un UNICO dia calendario: los limites
  // se construyen en UTC partiendo de la fecha Berlin (si se usara setHours local,
  // la comparacion DATE vs timestamptz colaria ademas el dia anterior).
  const p = berlinParts(date);
  const [y, m, d] = p.date.split('-');
  const ymd = `${y}-${m}-${d}`;
  return {
    start: new Date(`${ymd}T00:00:00.000Z`),
    end: new Date(`${ymd}T23:59:59.999Z`),
  };
}

/** Fecha actual en Europa/Berlin + desfase horario (CET/CEST) en formato +HH:mm. */
function berlinParts(now = new Date()): { date: string; hh: string; mm: string; offset: string } {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Berlin',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
  const hh = p.hour === '24' ? '00' : p.hour;
  const wallEpoch = Date.UTC(+p.year, +p.month - 1, +p.day, +hh, +p.minute, +p.second);
  const offsetMs = wallEpoch - now.getTime();
  const sign = offsetMs < 0 ? '-' : '+';
  const abs = Math.abs(offsetMs);
  const oh = String(Math.floor(abs / 3_600_000)).padStart(2, '0');
  const om = String(Math.floor((abs % 3_600_000) / 60_000)).padStart(2, '0');
  return { date: `${p.year}-${p.month}-${p.day}`, hh, mm: p.minute, offset: `${sign}${oh}:${om}` };
}

const norm = (s: unknown): string =>
  String(s ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');

const parseIso = (iso: string | null | undefined): number | null =>
  iso ? Date.parse(iso) || null : null;

export async function getBoard(eva: string, when?: string): Promise<DbRestDeparture[]> {
  // Clave por hora: un tablero de 17:00 cubre las salidas 17:00-17:59 y se
  // reutiliza entre numeros de tren y tiempo real (menos llamadas al proveedor).
  const hour = when ? `${when.slice(0, 13)}:00` : undefined;
  const key = hour ? `rt:board:${eva}:${hour}` : `rt:board:${eva}:${berlinParts().date}`;
  const cached = await redis.get(key).catch(() => null);
  if (cached) {
    try {
      return JSON.parse(cached) as DbRestDeparture[];
    } catch {
      /* reintenta desde la red */
    }
  }
  let lastErr: Error | null = null;
  for (const provider of realtimeProviders()) {
    try {
      const data = await provider.departures(eva, hour);
      await redis.set(key, JSON.stringify(data), 'EX', RT_BOARD_TTL).catch(() => {});
      realtimeStatus.lastOk = new Date().toISOString();
      realtimeStatus.lastError = null;
      realtimeStatus.provider = provider.name;
      realtimeStatus.enabled = true;
      return data;
    } catch (err) {
      lastErr = err as Error;
    }
  }
  throw lastErr ?? new Error('Echtzeit nicht verfügbar');
}

/** Numero de tren/linea extraido de un nombre "ICE 25" -> "25". */
const numberFrom = (name: string | null | undefined): string | null => {
  const m = String(name ?? '').match(/(\d{1,5})\s*$/);
  return m?.[1] ?? null;
};

function scoreCandidate(
  dep: DbRestDeparture,
  routeName: string,
  destinationStop: string,
  originStop: string,
  plannedEpoch: number,
): number {
  const lineName = dep.line?.name ?? '';
  const routeNumber = numberFrom(routeName);
  const lineNumber = dep.line?.lineNumber ?? numberFrom(lineName);
  const productShort = dep.line?.productShort ?? null;
  let score = 0;

  // Numero de linea HAFAS (linienNummer) == numero de la ruta GTFS (p. ej. "25").
  if (lineNumber && routeNumber && lineNumber === routeNumber) score += 800;
  else if (norm(lineName) === norm(routeName)) score += 400;
  else if (norm(lineName).includes(norm(routeName)) || norm(routeName).includes(norm(lineName))) score += 200;

  // Producto HAFAS (ICE/IC/EC) coincide con el prefijo de la ruta.
  const productNorm = norm(productShort);
  if (productNorm && norm(routeName).startsWith(productNorm)) score += 200;

  if (dep.cancelled) score -= 50;

  // Terminus / destino.
  const dirNorm = norm(dep.direction);
  if (dirNorm && (dirNorm.includes(norm(destinationStop)) || norm(destinationStop).includes(dirNorm))) {
    score += 300;
  }

  // Origen (solo disponible en v6, no en el tablero HAFAS de bahn.de).
  const originNorm = norm(dep.origin);
  if (originNorm && (originNorm.includes(norm(originStop)) || norm(originStop).includes(originNorm))) {
    score += 150;
  }

  const depEpoch = parseIso(dep.plannedWhen) ?? parseIso(dep.stop?.plannedDeparture);
  if (depEpoch) {
    const diff = Math.abs(depEpoch - plannedEpoch) / 60_000;
    if (diff > 90) score -= diff * 10;
    else score += 90 - diff;
  } else {
    score -= 200;
  }
  return score;
}

function toDto(
  trip: { id: string; routeName: string; originStop: string; destinationStop: string },
  date: string,
  plannedDeparture: string,
  eva: string,
  dep: DbRestDeparture,
  stationName: string,
): LiveRealtimeDto {
  const stop = dep.stop;
  const actualDeparture = dep.when ?? dep.stop?.departure ?? null;
  const plannedDepartureIso = dep.plannedWhen ?? dep.stop?.plannedDeparture ?? null;
  const delay =
    dep.delay ?? dep.stop?.departureDelay ?? (dep.stop?.arrivalDelay != null ? dep.stop.arrivalDelay : null);

  const delayMinutes = delay != null ? Math.round(delay / 60) : null;

  return {
    available: true,
    provider: realtimeStatus.provider,
    fetchedAt: new Date().toISOString(),
    train: {
      id: trip.id,
      routeName: trip.routeName,
      originStop: trip.originStop,
      destinationStop: trip.destinationStop,
      plannedDeparture,
      date,
    },
    live: {
      eva,
      station: stop?.station?.name || stationName,
      line: dep.line?.name ?? trip.routeName,
      number: numberFrom(dep.line?.name),
      direction: dep.direction ?? trip.destinationStop,
      origin: dep.origin ?? trip.originStop,
      delayMinutes,
      cancelled: !!dep.cancelled,
      plannedDeparture: plannedDepartureIso,
      actualDeparture,
      platform: dep.platform ?? dep.stop?.departurePlatform ?? null,
      plannedPlatform: dep.plannedPlatform ?? dep.stop?.plannedDeparturePlatform ?? null,
      position: latLngOf(dep.currentTripPosition as object | null | undefined),
      nextStops: (dep.nextStopovers ?? []).slice(0, 5).map((s) => {
        const row = s.stop ?? {};
        const arrivalDelay = row.arrivalDelay;
        return {
          name: row.station?.name ?? null,
          planned: row.plannedArrival ?? null,
          actual: row.arrival ?? null,
          delayMinutes: arrivalDelay != null ? Math.round(arrivalDelay / 60) : null,
          cancelled: !!row.cancelled,
          platform: row.arrivalPlatform ?? null,
        };
      }),
    },
  };
}

interface PlannedStop {
  stopName: string;
  time: string;
  plannedIso: string;
  plannedEpoch: number;
}

/** Hora de paso en una parada, ya normalizada (soporta GTFS >= 24:00). */
function plannedAt(
  stop: { departureTime: string | null; arrivalTime: string | null },
  date: string,
): Omit<PlannedStop, 'stopName'> | null {
  const time = stop.departureTime ?? stop.arrivalTime;
  if (!time) return null;
  const [hStr, mStr] = String(time).split(':');
  const hRaw = Number.parseInt(hStr, 10);
  if (Number.isNaN(hRaw)) return null;
  const extraDays = Math.floor(hRaw / 24);
  const hh = String(((hRaw % 24) + 24) % 24).padStart(2, '0');
  const mm = String(mStr ?? '00').padStart(2, '0');
  const boardDate = addDaysIso(date, extraDays);
  return {
    time: `${hh}:${mm}`,
    plannedIso: `${boardDate}T${hh}:${mm}:00`,
    plannedEpoch: epochOfBerlinLocal(date, time),
  };
}

/**
 * Estado en tiempo real de un viaje.
 *
 * - Si el viaje pasa por Fulda se usa su tablero (comportamiento histórico).
 * - Si no, se resuelve la estación de referencia (la próxima parada del día) a
 *   un ID EVA y se consulta el tablero de esa estación: así funciona en toda
 *   Alemania, no solo en el corredor de Fulda.
 *
 * Devuelve siempre un DTO serializable (nunca lanza) para que la web no se rompa
 * si el tablero en vivo no esta disponible (servicio caido / sin datos de hoy).
 */
export async function getLiveForTrip(
  tripId: string,
  date?: string,
): Promise<LiveRealtimeDto> {
  if (env.REALTIME_PROVIDER === 'off') {
    return { available: false, provider: 'off', reason: 'disabled', message: 'Echtzeit deaktiviert.' };
  }

  const trip = await prisma.trip.findUnique({
    where: { id: tripId },
    include: { stops: { orderBy: { stopSequence: 'asc' } } },
  });
  if (!trip) return { available: false, provider: realtimeStatus.provider, message: 'Fahrt nicht gefunden.' };

  const today = berlinParts().date;
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return { available: false, provider: realtimeStatus.provider, message: 'Ungültiges Datum.' };
  }
  const d = date ?? today;
  if (d !== today) {
    return {
      available: false,
      provider: realtimeStatus.provider,
      reason: 'not-today',
      message: 'Echtzeit ist nur am Reisetag verfügbar.',
    };
  }

  const { start, end } = dayRange(new Date(d));
  const activeDates = await prisma.tripServiceDate.count({
    where: { tripId, serviceDate: { gte: start, lte: end } },
  });
  if (activeDates === 0) {
    return { available: false, provider: realtimeStatus.provider, reason: 'not-running-today', message: 'Dieser Zug verkehrt heute nicht.' };
  }

  const plannedStops: PlannedStop[] = [];
  for (const stop of trip.stops) {
    const planned = plannedAt(stop, d);
    if (planned && !Number.isNaN(planned.plannedEpoch)) {
      plannedStops.push({ ...planned, stopName: stop.stopName });
    }
  }
  if (plannedStops.length === 0) {
    return { available: false, provider: realtimeStatus.provider, message: 'Für diese Fahrt fehlen planmäßige Zeiten.' };
  }

  const now = Date.now();
  // Fahrt beendet: das letzte Fahrplan-Ereignis liegt > 45 Min zurueck. Ohne
  // diese Pruefung wuerde ein alter Abfahrts-Zettel als "live" verkauft.
  const lastEpoch = plannedStops[plannedStops.length - 1].plannedEpoch;
  if (lastEpoch < now - 45 * 60_000) {
    return {
      available: false,
      provider: realtimeStatus.provider,
      reason: 'finished',
      message: 'Diese Fahrt ist bereits beendet.',
    };
  }

  // La Anzeigetafel liefert ABFAHRTEN: der Endbahnhof taugt nicht als Referenz
  // (dort kaeme ein anderer Zug derselben Linie in Gegenrichtung in Frage).
  const boardStops = plannedStops.length > 1 ? plannedStops.slice(0, -1) : plannedStops;
  // "Hauptbahnhof" sin ciudad (p. ej. Erfurt im DELFI-Feed) no sirve como
  // referencia: se salta para no consultar el tablero de otra ciudad.
  const usable = boardStops.filter((s) => !isGenericStationName(s.stopName));
  const pool = usable.length > 0 ? usable : boardStops;
  const upcoming = pool.find((s) => s.plannedEpoch >= now - 45 * 60_000);
  const reference = upcoming ?? pool[pool.length - 1];
  const fulda = pool.find((s) => s.stopName.toLowerCase().includes('fulda'));

  let eva: string;
  let stationName: string;
  if (fulda) {
    eva = env.REALTIME_FULDA_EVA;
    stationName = fulda.stopName;
  } else {
    const station = await resolveStationEva(reference.stopName);
    if (!station) {
      return {
        available: false,
        provider: realtimeStatus.provider,
        reason: 'no-station',
        message: `Keine Live-Anzeigetafel für ${reference.stopName} verfügbar.`,
      };
    }
    eva = station.id;
    stationName = station.name || reference.stopName;
  }

  const planned = fulda ?? reference;
  const plannedDeparture = `${d}T${planned.time}:00`;

  try {
    const downKey = `rt:down:${eva}`;
    const down = await redis.get(downKey).catch(() => null);
    if (down) {
      return { available: false, provider: realtimeStatus.provider, reason: 'upstream', message: down };
    }

    const board = await getBoard(eva, planned.plannedIso);
    realtimeStatus.lastFetchedAt = new Date().toISOString();

    const ranked = board
      .map((dep) => ({
        dep,
        score: scoreCandidate(dep, trip.routeName, trip.destinationStop, trip.originStop, planned.plannedEpoch),
      }))
      .sort((a, b) => b.score - a.score);

    const best = ranked[0];
    if (!best || best.score < 400) {
      realtimeStatus.lastError = 'Keine Übereinstimmungen auf der Live-Anzeigetafel';
      return {
        available: false,
        provider: realtimeStatus.provider,
        reason: 'no-match',
        message: `Der Zug erscheint derzeit nicht auf der Live-Anzeigetafel in ${stationName}.`,
      };
    }

    const dto = toDto(trip, d, plannedDeparture, eva, best.dep, stationName);
    sseBroker.broadcastToTrip(tripId, 'realtime', dto);

    // Disrupciones proactivas: cancelacion o retraso critico detectados en vivo.
    if (dto.live && shouldHandleRealtime({ cancelled: dto.live.cancelled, delayMinutes: dto.live.delayMinutes })) {
      void handleTripDisruption(tripId, {
        cancelled: dto.live.cancelled,
        delayMinutes: dto.live.delayMinutes,
      })
        .then((r) => {
          if (r.reassigned > 0 || r.pendingRefund > 0 || r.notified > 0) {
            // eslint-disable-next-line no-console
            console.log(
              `[disruption] ${tripId}: ${r.reason} – ${r.reassigned} umgebucht, ${r.pendingRefund} Rückerstattung, ${r.notified} benachrichtigt`,
            );
          }
        })
        .catch(() => undefined);
    }
    return dto;
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Upstream-Fehler';
    realtimeStatus.lastError = message;
    await redis.set(`rt:down:${eva}`, message, 'EX', 30).catch(() => {});
    return {
      available: false,
      provider: realtimeStatus.provider,
      reason: 'upstream',
      message: `Echtzeit nicht verfügbar: ${message}`,
    };
  }
}

const liveInflight = new Set<string>();

/**
 * Empuje en vivo: re-consulta Fulda cada REALTIME_POLL_MS (minimo 10 s) para los
 * viajes con clientes SSE conectados y difunde via sseBroker (evento 'realtime').
 * Sin paginas abiertas el poller no hace nada (no consume la fuente). Cada viaje
 * comparte el tablero de su hora en cache -> 1 llamada de red por hora y tick.
 */
export function startRealtimePoller(): NodeJS.Timeout {
  const periodMs = Math.max(10_000, env.REALTIME_POLL_MS);
  const timer = setInterval(() => {
    for (const tripId of sseBroker.activeTripIds()) {
      if (liveInflight.has(tripId)) continue;
      liveInflight.add(tripId);
      void getLiveForTrip(tripId)
        .catch(() => undefined)
        .finally(() => liveInflight.delete(tripId));
    }
  }, periodMs);
  timer.unref?.();
  return timer;
}