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
 * Hora de presentación a partir de un tiempo GTFS ("HH:MM[:SS]").
 * GTFS permite horas >= 24 para viajes que cruzan medianoche ("25:24" = 01:24
 * del día siguiente). Para mostrar se normaliza a 00-23 y, si cruza el día, se
 * añade el marcador "+1".
 */
export function formatClockTime(time: string | null | undefined, markNextDay = true): string {
  if (!time) return '--:--';
  const [hStr, mStr] = String(time).split(':');
  const hRaw = Number.parseInt(hStr, 10);
  if (Number.isNaN(hRaw)) return String(time);
  const h = ((hRaw % 24) + 24) % 24;
  const out = `${String(h).padStart(2, '0')}:${String(mStr ?? '00').padStart(2, '0')}`;
  return markNextDay && hRaw >= 24 ? `${out} +1` : out;
}

function addDaysIso(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export { addDaysIso };

/** Desfase de Europe/Berlin en una fecha ("+02:00" en verano, "+01:00" en invierno). */
export function berlinOffsetFor(dateStr: string, time = '12:00'): string {
  const epoch = epochOfBerlinLocal(dateStr, time);
  const utc = Date.parse(`${dateStr}T${time}:00Z`);
  if (Number.isNaN(epoch) || Number.isNaN(utc)) return '+01:00';
  // epoch es el instante UTC de la hora local; el desfase es UTC - local.
  const diffMin = Math.round((utc - epoch) / 60_000);
  const sign = diffMin < 0 ? '-' : '+';
  const abs = Math.abs(diffMin);
  const hh = String(Math.floor(abs / 60)).padStart(2, '0');
  const mm = String(abs % 60).padStart(2, '0');
  return `${sign}${hh}:${mm}`;
}

/**
 * Epoch (ms UTC) de una hora local Berlin "HH:MM[:SS]" en una fecha dada.
 * Maneja correctamente CET/CEST (el desfase se deriva de la propia fecha) y
 * las horas GTFS >= 24 (la fecha de servicio avanza al día siguiente).
 */
export function epochOfBerlinLocal(dateStr: string, time: string): number {
  const [hStr, mStr] = String(time).split(':');
  const hRaw = Number.parseInt(hStr, 10);
  if (Number.isNaN(hRaw)) return Number.NaN;
  const extraDays = Math.floor(hRaw / 24);
  const hh = String(((hRaw % 24) + 24) % 24).padStart(2, '0');
  const mm = String(mStr ?? '00').padStart(2, '0');
  const serviceDate = extraDays > 0 ? addDaysIso(dateStr, extraDays) : dateStr;
  const wallUtc = Date.parse(`${serviceDate}T${hh}:${mm}:00Z`);
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
