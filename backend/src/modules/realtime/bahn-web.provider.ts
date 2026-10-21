/**
 * Proveedor de tiempo real "bahn-web": tablero HAFAS oficial de Deutsche Bahn
 * servido por www.bahn.de (endpoint publico /web/api/reiseloesung/abfahrten).
 *
 * Sin clave y devuelve exactamente lo que muestra DB Navigator en la estacion:
 * numero de tren (p. ej. "ICE 786"), linea (linienNummer), retraso (real vs
 * planeado), via, terminus y avisos RIS. Normaliza la respuesta al mismo tipo
 * DbRestDeparture para que el resto del modulo sea agnóstico del proveedor.
 */

import { execFile } from 'node:child_process';
import type { DbRestDeparture, RealtimeProvider } from './db-rest.provider';

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
const LONG_DISTANCE = new Set(['ICE', 'IC', 'EC', 'TGV', 'NJ', 'RJ']);

const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

/**
 * bahn.de protege el endpoint con un anti-bot OPS que bloquea la pila TLS de
 * Node (OpenSSL), pero admite Schannel (.NET/PowerShell). En Windows este puente
 * ejecuta powershell.exe + Invoke-RestMethod y parsea su JSON; fuera de Windows
 * se intenta fetch simple por si en ese entorno no hay bloqueo.
 */
function psInvokeRestMethod(url: string): Promise<string> {
  const script =
    "$ErrorActionPreference='Stop';" +
    '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;' +
    `(Invoke-RestMethod -Uri '${url}' -Method Get -TimeoutSec 15 ` +
    `-Headers @{Accept='application/json';'User-Agent'='${USER_AGENT}';Referer='https://www.bahn.de/'}) ` +
    '| ConvertTo-Json -Depth 12 -Compress';
  return new Promise((resolve, reject) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', script],
      { timeout: 25_000, maxBuffer: 8 * 1024 * 1024, windowsHide: true, encoding: 'utf8' },
      (err, stdout) => {
        if (err) return reject(err);
        if (!stdout.trim()) return reject(new Error('Leere Antwort von PowerShell'));
        resolve(stdout);
      },
    );
  });
}

async function plainFetch(url: string): Promise<unknown> {
  const res = await fetch(url, {
    headers: {
      Accept: 'application/json',
      'User-Agent': USER_AGENT,
      Referer: 'https://www.bahn.de/',
    },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`Anzeigetafel nicht verfügbar (HTTP ${res.status})`);
  return res.json();
}

/** Desfase UTC de Europe/Berlin para un instante dado, en "+HH:MM". */
function berlinOffsetFor(isoLocal: string): string {
  const date = new Date(isoLocal);
  const now = new Date();
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
  return `${sign}${String(Math.floor(abs / 3_600_000)).padStart(2, '0')}:${String(
    Math.floor((abs % 3_600_000) / 60_000),
  ).padStart(2, '0')}`;
}

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
        const raw =
          process.platform === 'win32'
            ? await psInvokeRestMethod(url).catch(() => plainFetch(url))
            : await plainFetch(url);
        const body = (raw && typeof raw === 'string' ? JSON.parse(raw) : raw) as {
          entries?: BahnWebEntry[];
        };
        if (!body || !Array.isArray(body.entries)) {
          throw new Error('Unerwartete Antwort der HAFAS-Anzeigetafel');
        }

        const offset = berlinOffsetFor(`${date}T00:00:00`);
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