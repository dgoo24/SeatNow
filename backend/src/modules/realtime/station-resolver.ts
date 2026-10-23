/**
 * Resolución nombre de estación (GTFS/DELFI) -> ID de estación DB (EVA).
 *
 * Los tableros en vivo de DB usan el ID EVA, mientras que el feed GTFS guarda
 * nombres. Se consulta primero la búsqueda de estaciones de bahn.de (funciona
 * en toda Alemania y devuelve el EVA en `extId`) y, como respaldo, el espejo
 * público v6/v5.db.transport.rest. El resultado se cachea en Redis.
 */

import { env } from '../../config/env';
import { redis } from '../../shared/redis/redis';
import { nearMatch } from '../../shared/text/fuzzy';
import { bahnGetJson } from './bahn-web-http';

export interface StationRef {
  id: string;
  name: string;
}

interface BahnOrt {
  extId?: string;
  id?: string;
  name?: string;
  type?: string;
}

interface LocationResult {
  type?: string;
  id?: string;
  name?: string;
}

const normalize = (value: unknown): string =>
  String(value ?? '')
    .toLowerCase()
    .replace(/ß/g, 'ss')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\b(hauptbahnhof|hauptbf)\b/g, 'hbf')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');

/** Quita calificadores entre paréntesis: "Hamburg-Altona(S)" -> "Hamburg-Altona". */
const cleanStationName = (value: unknown): string =>
  normalize(String(value ?? '').replace(/\([^)]*\)/g, ' '));

function cacheKey(stopName: string): string {
  return `rt:eva:${normalize(stopName).replace(/ /g, '_')}`;
}

/**
 * "Hauptbahnhof" / "Hauptbahnhof A1" sin ciudad: el feed DELFI las usa para
 * algunas estaciones (p. ej. Erfurt Hbf) y no identifican una estacion unica.
 */
export function isGenericStationName(value: string): boolean {
  return /^(hbf|bahnhof)( [a-z0-9]{1,3})?$/.test(normalize(value));
}

function restHosts(): string[] {
  const custom = env.REALTIME_DB_REST_URL.trim();
  return custom ? [custom] : ['https://v6.db.transport.rest', 'https://v5.db.transport.rest'];
}

function scoreLocation(candidate: string, wanted: string): number {
  const cn = cleanStationName(candidate);
  const wn = cleanStationName(wanted);
  if (!cn || !wn) return 0;
  if (cn === wn) return 100;
  if (cn.startsWith(wn)) return 80;
  if (cn.includes(wn) || wn.includes(cn)) return 60;

  const ct = cn.split(' ');
  const wt = wn.split(' ');
  let hits = 0;
  for (const token of wt) {
    if (ct.includes(token)) hits += 1;
    else if (token.length >= 2 && ct.some((t) => t.startsWith(token))) hits += 0.75;
    else if (token.length >= 4 && ct.some((t) => nearMatch(t, token))) hits += 0.6;
  }
  return (hits / wt.length) * 50;
}

function pickBest(
  candidates: Array<{ id: string; name: string }>,
  wanted: string,
  minScore = 50,
): StationRef | null {
  let best: StationRef | null = null;
  let bestScore = 0;
  for (const candidate of candidates) {
    const score = scoreLocation(candidate.name, wanted);
    if (score > bestScore) {
      bestScore = score;
      best = candidate;
    }
  }
  return best && bestScore >= minScore ? best : null;
}

async function viaBahnDe(stopName: string): Promise<StationRef | null> {
  const url =
    'https://www.bahn.de/web/api/reiseloesung/orte?suchbegriff=' +
    encodeURIComponent(stopName) +
    '&mitVorschlaegen=true&typ=ALL&limit=10';
  const data = await bahnGetJson<BahnOrt[] | { ortListe?: BahnOrt[] }>(url);
  // PowerShell colapsa arrays de un solo elemento en un objeto: se normaliza.
  const list: BahnOrt[] = Array.isArray(data)
    ? data
    : Array.isArray(data.ortListe)
      ? data.ortListe
      : data && typeof data === 'object' && 'extId' in data
        ? [data as BahnOrt]
        : [];
  const stops = list
    .filter((item): item is BahnOrt & { extId: string; name: string } =>
      item.type === 'ST' && Boolean(item.extId) && Boolean(item.name),
    )
    .map((item) => ({ id: item.extId, name: item.name }));
  return pickBest(stops, stopName);
}

async function viaRest(stopName: string): Promise<StationRef | null> {
  const query = encodeURIComponent(stopName);
  for (const host of restHosts()) {
    const url = `${host}/locations?query=${query}&results=10&stops=true&addresses=false&poi=false&linesOfStops=false`;
    try {
      const res = await fetch(url, {
        headers: { Accept: 'application/json', 'User-Agent': 'trenes-mvp-demo/1.0' },
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) continue;
      const data = (await res.json()) as LocationResult[];
      const stops = (Array.isArray(data) ? data : [])
        .filter((item): item is LocationResult & { id: string; name: string } =>
          item.type === 'stop' && Boolean(item.id) && Boolean(item.name),
        )
        .map((item) => ({ id: item.id, name: item.name }));
      const best = pickBest(stops, stopName);
      if (best) return best;
    } catch {
      /* prueba el siguiente host */
    }
  }
  return null;
}

export async function resolveStationEva(stopName: string): Promise<StationRef | null> {
  // Nombre sin ciudad: cualquier tablero seria una suposicion.
  if (!stopName.trim() || isGenericStationName(stopName)) return null;
  const key = cacheKey(stopName);
  const cached = await redis.get(key).catch(() => null);
  if (cached) {
    try {
      const parsed = JSON.parse(cached) as StationRef | null;
      return parsed && parsed.id ? parsed : null;
    } catch {
      /* vuelve a consultar */
    }
  }

  let best: StationRef | null = null;
  try {
    best = await viaBahnDe(stopName);
  } catch {
    best = null;
  }
  if (!best) best = await viaRest(stopName);

  if (best) {
    await redis.set(key, JSON.stringify(best), 'EX', 60 * 60 * 24 * 30).catch(() => {});
    return best;
  }
  // Sin coincidencia fiable: cache negativa corta para no martillear el upstream.
  await redis.set(key, 'null', 'EX', 60 * 60).catch(() => {});
  return null;
}
