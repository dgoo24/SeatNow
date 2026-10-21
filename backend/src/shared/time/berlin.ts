/** Utilidades de tiempo en la zona del servicio (Europe/Berlin). */

const berlinFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Berlin',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function berlinParts(d = new Date()): { date: string; hh: string; mm: string; ss: string } {
  const p = Object.fromEntries(berlinFmt.formatToParts(d).map((x) => [x.type, x.value]));
  const hh = p.hour === '24' ? '00' : p.hour;
  return { date: `${p.year}-${p.month}-${p.day}`, hh, mm: p.minute, ss: p.second };
}

/** Fecha calendario Berlin (yyyy-mm-dd). */
export function berlinDateStr(d = new Date()): string {
  return berlinParts(d).date;
}

/** Minutos desde medianoche Berlin (para comparar con HH:MM del GTFS). */
export function berlinNowMinutes(d = new Date()): number {
  const { hh, mm } = berlinParts(d);
  return Number.parseInt(hh, 10) * 60 + Number.parseInt(mm, 10);
}

/**
 * Epoch (ms UTC) de una hora local Berlin "HH:MM[:SS]" en una fecha dada.
 * Maneja correctamente CET/CEST (el desfase se deriva de la propia fecha).
 */
export function epochOfBerlinLocal(dateStr: string, time: string): number {
  const hhmm = time.slice(0, 5);
  const wallUtc = Date.parse(`${dateStr}T${hhmm}:00Z`);
  if (Number.isNaN(wallUtc)) return Number.NaN;
  // Desfase: epoch "guess" -> hora de pared Berlin de ese instante.
  const p = Object.fromEntries(berlinFmt.formatToParts(new Date(wallUtc)).map((x) => [x.type, x.value]));
  const hour = p.hour === '24' ? '00' : p.hour;
  const wallOfGuess = Date.UTC(+p.year, +p.month - 1, +p.day, +hour, +p.minute, +p.second);
  return wallUtc + (wallUtc - wallOfGuess);
}

/** Rango UTC [start,end] de un dia calendario Berlin (para queries DATE). */
export function berlinDayUtcRange(dateStr: string): { start: Date; end: Date } {
  return {
    start: new Date(`${dateStr}T00:00:00.000Z`),
    end: new Date(`${dateStr}T23:59:59.999Z`),
  };
}
