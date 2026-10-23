/**
 * Proveedor de tiempo real "bahn-web": tablero HAFAS oficial de Deutsche Bahn
 * servido por www.bahn.de (endpoint publico /web/api/reiseloesung/abfahrten).
 *
 * Sin clave y devuelve exactamente lo que muestra DB Navigator en la estacion:
 * numero de tren (p. ej. "ICE 786"), linea (linienNummer), retraso (real vs
 * planeado), via, terminus y avisos RIS. Normaliza la respuesta al mismo tipo
 * DbRestDeparture para que el resto del modulo sea agnóstico del proveedor.
 */

import type { DbRestDeparture, RealtimeProvider } from './db-rest.provider';
import { bahnGetJson } from './bahn-web-http';
import { berlinOffsetFor } from '../../shared/time/berlin';

interface BahnWebEntry {
  bahnhofsId?: string;
  zeit?: string | null;
  ezZeit?: string | null;
  gleis?: string | null;
  journeyId?: string;
  meldungen?: { text?: string; type?: string }[];
  verkehrmittel?: {
    name?: string;
    linienNummer?: string | null;
    kurzText?: string;
    mittelText?: string;
    langText?: string;
    produktGattung?: string;
  };
  terminus?: string | null;
}

const BASE = 'https://www.bahn.de/web/api/reiseloesung/abfahrten';
const LONG_DISTANCE = new Set(['ICE', 'IC', 'EC', 'ECE', 'TGV', 'NJ', 'RJ', 'RJX', 'EN']);


const toIso = (s: string | null | undefined, offset: string): string | undefined =>
  s ? `${s}${offset}` : undefined;

function needsTimeout(entry: BahnWebEntry): boolean {
  const text = (entry.meldungen ?? []).map((m) => m.text ?? '').join(' ').toLowerCase();
  return text.includes('ausfall') || text.includes('entfällt') || text.includes('faellt aus');
}

export class BahnWebProvider implements RealtimeProvider {
  readonly name = 'bahn-web';
  private readonly hosts: string[];

  constructor(hosts: string[] = [BASE]) {
    this.hosts = hosts;
  }

  /**
   * Consulta el tablero de salidas. `when` es ISO local Berlin "YYYY-MM-DDTHH:mm"
   * (p. ej. "2026-10-11T17:00"); sin el parametro, se usa la hora actual.
   */
  async departures(eva: string, when?: string): Promise<DbRestDeparture[]> {
    const now = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Berlin',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
    const p = Object.fromEntries(now.formatToParts(new Date()).map((x) => [x.type, x.value]));
    const hour = p.hour === '24' ? '00' : p.hour;
    const date = when?.slice(0, 10) ?? `${p.year}-${p.month}-${p.day}`;
    const time = when?.slice(11, 16) ?? `${hour}:${p.minute}`;

    const params = new URLSearchParams({ ortExtId: eva, datum: date, zeit: `${time}:00` });
    let lastErr: Error | null = null;

    for (const host of this.hosts) {
      try {
        const url = `${host}?${params.toString()}`;
        const body = await bahnGetJson<{ entries?: BahnWebEntry[] }>(url);
        if (!body || !Array.isArray(body.entries)) {
          throw new Error('Unerwartete Antwort der HAFAS-Anzeigetafel');
        }

        const offset = berlinOffsetFor(date, time);
        const out: DbRestDeparture[] = [];
        for (const e of body.entries) {
          const kurzText = e.verkehrmittel?.kurzText ?? '';
          if (!LONG_DISTANCE.has(kurzText)) continue;
          const planned = toIso(e.zeit, offset);
          const actual = toIso(e.ezZeit ?? e.zeit, offset);
          const delaySec = planned && actual ? Math.round((Date.parse(actual) - Date.parse(planned)) / 1000) : 0;

          out.push({
            tripId: e.journeyId ?? undefined,
            plannedWhen: planned,
            when: actual,
            delay: delaySec,
            platform: e.gleis ?? null,
            plannedPlatform: e.gleis ?? null,
            cancelled: needsTimeout(e),
            line: {
              name: e.verkehrmittel?.name ?? e.verkehrmittel?.mittelText ?? undefined,
              product: e.verkehrmittel?.produktGattung ?? kurzText,
              productShort: kurzText,
              lineNumber: e.verkehrmittel?.linienNummer || undefined,
            },
            direction: e.terminus ?? undefined,
            stop: { station: { id: e.bahnhofsId ?? eva, name: '' } },
          });
        }
        return out;
      } catch (err) {
        lastErr = err as Error;
      }
    }
    throw lastErr ?? new Error('Echtzeit nicht verfügbar');
  }
}