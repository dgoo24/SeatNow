/**
 * Búsqueda de estaciones por nombre oficial, alias y errores de escritura.
 *
 * El feed GTFS de DELFI usa nombres oficiales ("Freiburg, Hauptbahnhof",
 * "Frankfurt(Main)Hbf", "München Hbf"). El usuario, en cambio, escribe
 * "Freiburg im Breisgau", "Frankfurt Hbf", "Munich" o "Koeln". Aquí se
 * normalizan ambos lados y se puntúa la coincidencia para que el autocompletado
 * y la búsqueda encuentren la estación correcta.
 */

import { nearMatch } from '../../shared/text/fuzzy';

export interface StopEntry {
  name: string;
  norm: string;
  tokens: string[];
  variants: string[];
}

/** Nombres alternativos de ciudades (inglés, variantes sin diéresis, etc.). */
const CITY_ALIASES: Array<{ city: string; aliases: string[] }> = [
  { city: 'munchen', aliases: ['munich', 'muenchen'] },
  { city: 'koln', aliases: ['cologne', 'koeln'] },
  { city: 'nurnberg', aliases: ['nuremberg', 'nuernberg'] },
  { city: 'hannover', aliases: ['hanover'] },
  { city: 'braunschweig', aliases: ['brunswick'] },
  { city: 'regensburg', aliases: ['ratisbon'] },
  { city: 'mainz', aliases: ['mayence'] },
  { city: 'koblenz', aliases: ['coblenz'] },
  { city: 'aachen', aliases: ['aix la chapelle', 'aix'] },
  { city: 'trier', aliases: ['treves'] },
  { city: 'freiburg', aliases: ['freiburg im breisgau', 'freiburg breisgau', 'freiburg ib'] },
  {
    city: 'frankfurt main',
    aliases: ['frankfurt', 'frankfurt hbf', 'frankfurt main', 'frankfurt am main'],
  },
  { city: 'dusseldorf', aliases: ['duesseldorf'] },
  { city: 'munster', aliases: ['muenster'] },
  { city: 'lubeck', aliases: ['luebeck'] },
  { city: 'saarbrucken', aliases: ['saarbruecken'] },
  { city: 'wurzburg', aliases: ['wuerzburg'] },
  { city: 'tubingen', aliases: ['tuebingen'] },
  { city: 'gottingen', aliases: ['goettingen'] },
  { city: 'osnabruck', aliases: ['osnabrueck'] },
  { city: 'monchengladbach', aliases: ['moenchengladbach'] },
  { city: 'luneburg', aliases: ['lueneburg'] },
  { city: 'flensburg', aliases: ['flensborg'] },
  { city: 'wien', aliases: ['vienna'] },
];

const STATION_SUFFIXES = new Set(['hbf', 'bf', 'bahnhof', 'hauptbahnhof']);

/** Normaliza mayúsculas, diéresis, ß y puntuación ("München Hbf" -> "munchen hbf"). */
export function normalizeStopText(input: string): string {
  return String(input ?? '')
    .toLowerCase()
    .replace(/ß/g, 'ss')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\b(hauptbahnhof|hauptbf)\b/g, 'hbf')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

/** Variante con diéresis escritas como ue/oe/ae ("muenchen" -> "munchen"). */
function collapseUmlauts(norm: string): string {
  return norm.replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u');
}

function aliasesFor(suffixless: string): string[] {
  const out: string[] = [];
  for (const entry of CITY_ALIASES) {
    if (suffixless === entry.city) {
      for (const alias of entry.aliases) out.push(normalizeStopText(alias));
    }
  }
  return out;
}

export function buildStopEntry(name: string): StopEntry {
  const norm = normalizeStopText(name);
  const variants = new Set<string>([norm]);

  const collapsed = collapseUmlauts(norm);
  if (collapsed !== norm) variants.add(collapsed);

  // Ciudad sin sufijo de estación ("frankfurt main hbf" -> "frankfurt main").
  const parts = norm.split(' ');
  while (parts.length > 1 && STATION_SUFFIXES.has(parts[parts.length - 1] ?? '')) parts.pop();
  const suffixless = parts.join(' ');
  if (suffixless && suffixless !== norm) variants.add(suffixless);

  // Ciudad sola ("frankfurt", "berlin"): permite listar todas las estaciones.
  const city = parts[0] ?? '';
  if (city.length >= 3) variants.add(city);

  const hasHbf = norm.endsWith(' hbf');
  for (const alias of aliasesFor(suffixless)) {
    if (!alias) continue;
    variants.add(alias);
    variants.add(collapseUmlauts(alias));
    if (hasHbf) {
      variants.add(`${alias} hbf`);
      variants.add(`${collapseUmlauts(alias)} hbf`);
    }
  }

  const tokens = new Set<string>();
  for (const v of variants) {
    for (const t of v.split(' ')) if (t) tokens.add(t);
  }
  for (const t of norm.split(' ')) if (t) tokens.add(t);

  return { name, norm, tokens: [...tokens], variants: [...variants] };
}

/**
 * Puntuación 0..1000 de una estación frente a la consulta.
 * - 1000: nombre/variante exacta.
 * - 900+: prefijo (sirve para resolver texto libre a una estación).
 * - 350+: coincidencia parcial/token/tolerancia a erratas.
 */
export function scoreStop(entry: StopEntry, qNorm: string, qTokens: string[]): number {
  let best = 0;
  for (const variant of entry.variants) {
    if (variant === qNorm) return 1000;
    if (variant.startsWith(qNorm)) {
      best = Math.max(best, 900 - Math.min(100, variant.length - qNorm.length));
    } else {
      const idx = variant.indexOf(qNorm);
      if (idx > 0) best = Math.max(best, 700 - Math.min(100, idx * 2));
    }
  }
  if (qTokens.length === 0) return best;

  let hits = 0;
  for (const qt of qTokens) {
    if (entry.tokens.includes(qt)) hits += 1;
    else if (qt.length >= 2 && entry.tokens.some((t) => t.startsWith(qt))) hits += 0.75;
    else if (qt.length >= 4 && entry.tokens.some((t) => nearMatch(t, qt))) hits += 0.8;
  }
  const ratio = hits / qTokens.length;
  if (ratio >= 0.4) best = Math.max(best, Math.round(500 * ratio));
  return best;
}

export function buildStopIndex(names: string[]): StopEntry[] {
  return names.map(buildStopEntry);
}

export interface ScoredStop {
  entry: StopEntry;
  score: number;
}

export function rankStops(entries: StopEntry[], query: string, minScore = 350): ScoredStop[] {
  const qNorm = normalizeStopText(query);
  if (!qNorm) return [];
  const qTokens = qNorm.split(' ');
  return entries
    .map((entry) => ({ entry, score: scoreStop(entry, qNorm, qTokens) }))
    .filter((s) => s.score >= minScore)
    .sort(
      (a, b) =>
        b.score - a.score ||
        Number(!a.entry.norm.includes(' hbf')) - Number(!b.entry.norm.includes(' hbf')) ||
        a.entry.name.length - b.entry.name.length ||
        a.entry.name.localeCompare(b.entry.name),
    );
}
