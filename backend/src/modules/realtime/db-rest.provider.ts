/**
 * Proveedor de tiempo real "db-rest": datos reales de DB (retrasos, vias,
 * cancelaciones, paradas siguientes y posicion del tren) a traves del espejo
 * publico v6/v5.db.transport.rest (los mismos datos que la app DB Navigator).
 *
 * No requiere clave. Si el servicio esta caido (codigo 503 global), el modulo
 * devuelve available:false en vez de bloquear la web.
 */

import { env } from '../../config/env';

export interface RealtimeProviderError extends Error {
  upstream: boolean;
}

export interface DbRestDeparture {
  tripId?: string;
  stop?: {
    station?: { id?: string; name?: string };
    plannedDeparture?: string;
    departure?: string;
    departureDelay?: number | null;
    departurePlatform?: string | null;
    plannedDeparturePlatform?: string | null;
    plannedArrival?: string;
    arrival?: string;
    arrivalDelay?: number | null;
    cancelled?: boolean;
  };
  currentStop?: { station?: { id?: string; name?: string } };
  line?: {
    name?: string;
    product?: string;
    type?: string;
    /** Numero de linea (p. ej. "25" en "ICE 786", linienNummer en HAFAS). */
    lineNumber?: string;
    /** Producto corto HAFAS (p. ej. "ICE", "IC", "EC"). */
    productShort?: string;
  };
  direction?: string;
  origin?: string;
  when?: string;
  plannedWhen?: string;
  delay?: number | null;
  platform?: string | null;
  plannedPlatform?: string | null;
  cancelled?: boolean;
  currentTripPosition?:
    | { kind: 'latlon'; latitude: number; longitude: number; realtime?: boolean }
    | { kind: 'stop'; id?: string; name?: string };
  nextStopovers?: {
    stop?: {
      station?: { id?: string; name?: string };
      plannedArrival?: string;
      arrival?: string;
      arrivalDelay?: number | null;
      cancelled?: boolean;
      arrivalPlatform?: string | null;
    };
  }[];
}

export interface RealtimeProvider {
  readonly name: string;
  /** Tablero de salidas de una estacion (solo larga distancia) desde `when` (ISO Berlin) o ahora. */
  departures(eva: string, when?: string): Promise<DbRestDeparture[]>;
}

export class DbRestProvider implements RealtimeProvider {
  readonly name = 'db-rest';
  private readonly hosts: string[];

  constructor() {
    this.hosts = env.REALTIME_DB_REST_URL.trim()
      ? [env.REALTIME_DB_REST_URL.trim()]
      : ['https://v6.db.transport.rest', 'https://v5.db.transport.rest'];
  }

  async departures(eva: string, _when?: string): Promise<DbRestDeparture[]> {
    const params = new URLSearchParams({
      duration: '420',
      nationalExpress: 'true',
      national: 'true',
      regional: 'false',
      suburban: 'false',
      bus: 'false',
      ferry: 'false',
      tram: 'false',
      taxi: 'false',
    });

    let lastErr: RealtimeProviderError | null = null;
    for (const host of this.hosts) {
      const url = `${host}/stations/${encodeURIComponent(eva)}/departures?${params.toString()}`;
      try {
        const res = await fetch(url, {
          headers: {
            Accept: 'application/json',
            'User-Agent': 'trenes-mvp-demo/1.0',
          },
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok) {
          const err = new Error(
            `Live-Anzeigetafel nicht verfügbar (HTTP ${res.status})`,
          ) as RealtimeProviderError;
          err.upstream = true;
          lastErr = err;
          continue;
        }
        const data = (await res.json()) as DbRestDeparture[];
        if (!Array.isArray(data)) {
          throw new Error('Unerwartete Antwort der Live-Anzeigetafel');
        }
        return data;
      } catch (err) {
        const e = err as RealtimeProviderError;
        if (e.name === 'AbortError') {
          const timeout = new Error('Die Live-Anzeigetafel hat nicht rechtzeitig geantwortet') as RealtimeProviderError;
          timeout.upstream = true;
          lastErr = timeout;
          continue;
        }
        if (!lastErr) {
          const wrapped = new Error(
            `No se pudo consultar el tiempo real: ${e.message}`,
          ) as RealtimeProviderError;
          wrapped.upstream = true;
          lastErr = wrapped;
        }
      }
    }
    throw lastErr ?? new Error('Echtzeit nicht verfügbar');
  }
}