import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import AdmZip from 'adm-zip';
import { parse } from 'csv-parse/sync';

export interface GtfsStop {
  stop_id: string;
  stop_name: string;
  stop_lat?: number;
  stop_lon?: number;
}

export interface GtfsRoute {
  route_id: string;
  route_short_name?: string;
  route_long_name?: string;
  route_type?: number;
}

export interface GtfsTrip {
  route_id: string;
  service_id: string;
  trip_id: string;
  trip_headsign?: string;
  direction_id?: string;
}

export interface GtfsStopTime {
  trip_id: string;
  arrival_time: string;
  departure_time: string;
  stop_id: string;
  stop_sequence: number;
  stop_headsign?: string;
}

/** calendar.txt -> dias activos de lunes a domingo + ventana de fechas */
export interface GtfsServiceCalendar {
  service_id: string;
  days: [boolean, boolean, boolean, boolean, boolean, boolean, boolean]; // Mon..Sun
  startDate: string; // YYYYMMDD
  endDate: string; // YYYYMMDD
}

/** calendar_dates.txt -> excepciones (1 = añadido, 2 = eliminado) */
export interface GtfsCalendarDate {
  service_id: string;
  date: string; // YYYYMMDD
  exception_type: 1 | 2;
}

export interface GtfsDataset {
  stops: GtfsStop[];
  routes: GtfsRoute[];
  trips: GtfsTrip[];
  stopTimes: GtfsStopTime[];
  calendars: Map<string, GtfsServiceCalendar>;
  calendarExceptions: Map<string, GtfsCalendarDate[]>;
}

const REQUIRED_FILES = ['stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt'];
const OPTIONAL_FILES = ['calendar.txt', 'calendar_dates.txt'];

type FileMap = Map<string, string>;

async function readDirectory(dir: string): Promise<FileMap> {
  const map: FileMap = new Map();
  for (const name of REQUIRED_FILES) {
    const full = path.join(dir, name);
    if (!fs.existsSync(full)) {
      throw new Error(`Erforderliche GTFS-Datei fehlt: ${name} in ${dir}`);
    }
    map.set(name, await fsp.readFile(full, 'utf8'));
  }
  for (const name of OPTIONAL_FILES) {
    const full = path.join(dir, name);
    if (fs.existsSync(full)) map.set(name, await fsp.readFile(full, 'utf8'));
  }
  return map;
}

async function readZip(zipPath: string): Promise<FileMap> {
  const zip = new AdmZip(zipPath);
  const map: FileMap = new Map();
  for (const name of [...REQUIRED_FILES, ...OPTIONAL_FILES]) {
    const entry = zip.getEntry(name);
    if (!entry) continue;
    if (REQUIRED_FILES.includes(name)) {
      map.set(name, entry.getData().toString('utf8'));
    } else {
      map.set(name, entry.getData().toString('utf8'));
    }
  }
  for (const name of REQUIRED_FILES) {
    if (!map.has(name)) throw new Error(`Erforderliche GTFS-Datei fehlt: ${name} im ZIP`);
  }
  return map;
}

async function readUrl(url: string): Promise<FileMap> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`No se pudo descargar el GTFS (${res.status}) desde ${url}`);
  const buffer = Buffer.from(await res.arrayBuffer());
  const tmp = path.join(os.tmpdir(), `gtfs-${Date.now()}.zip`);
  await fsp.writeFile(tmp, buffer);
  try {
    return await readZip(tmp);
  } finally {
    await fsp.unlink(tmp).catch(() => undefined);
  }
}

function rows<T>(content: string): T[] {
  return parse(content, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
    relax_column_count: true,
  }) as T[];
}

function normalizeTime(value: string | undefined): string {
  if (!value) return '00:00:00';
  const parts = value.split(':').map((p) => Number.parseInt(p, 10) || 0);
  const [h = 0, m = 0, s = 0] = parts;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function parseDateRange(d: string): Date {
  const year = Number.parseInt(d.slice(0, 4), 10);
  const month = Number.parseInt(d.slice(4, 6), 10) - 1;
  const day = Number.parseInt(d.slice(6, 8), 10);
  return new Date(Date.UTC(year, month, day));
}

function toIsoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

const DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const;

/**
 * Expande las fechas activas (ISO yyyy-mm-dd) de un service_id combinando
 * calendar.txt (semana base + ventana) con calendar_dates.txt (excepciones).
 * Si no existe calendar para el service, devuelve [] (compat: viaje valido cualquier dia).
 */
export function expandServiceDates(
  serviceId: string,
  calendars: Map<string, GtfsServiceCalendar>,
  exceptions: Map<string, GtfsCalendarDate[]>,
): string[] {
  const cal = calendars.get(serviceId);
  if (!cal) return [];

  const dates = new Set<string>();

  let current = parseDateRange(cal.startDate);
  const end = parseDateRange(cal.endDate);
  while (current.getTime() <= end.getTime()) {
    // JS getDay(): 0=Dom..6=Sab -> Indice array [Mon..Sun] = (jsDay + 6) % 7
    const jsDay = current.getUTCDay();
    const idx = (jsDay + 6) % 7;
    if (cal.days[idx]) dates.add(toIsoDate(current));
    current = new Date(current.getTime() + 86_400_000);
  }

  for (const exc of exceptions.get(serviceId) ?? []) {
    const iso = toIsoDate(parseDateRange(exc.date));
    if (exc.exception_type === 1) dates.add(iso);
    else dates.delete(iso);
  }

  return [...dates].sort();
}

export async function loadGtfsDataset(source: string): Promise<GtfsDataset> {
  let files: FileMap;
  if (/^https?:\/\//i.test(source)) {
    files = await readUrl(source);
  } else if (source.toLowerCase().endsWith('.zip')) {
    files = await readZip(path.resolve(process.cwd(), source));
  } else {
    files = await readDirectory(path.resolve(process.cwd(), source));
  }

  const stops = rows<Record<string, string>>(files.get('stops.txt')!).map((r) => ({
    stop_id: r.stop_id,
    stop_name: r.stop_name,
    stop_lat: r.stop_lat ? Number.parseFloat(r.stop_lat) : undefined,
    stop_lon: r.stop_lon ? Number.parseFloat(r.stop_lon) : undefined,
  }));

  const routes = rows<Record<string, string>>(files.get('routes.txt')!).map((r) => ({
    route_id: r.route_id,
    route_short_name: r.route_short_name,
    route_long_name: r.route_long_name,
    route_type: r.route_type ? Number.parseInt(r.route_type, 10) : undefined,
  }));

  const trips = rows<Record<string, string>>(files.get('trips.txt')!).map((r) => ({
    route_id: r.route_id,
    service_id: r.service_id,
    trip_id: r.trip_id,
    trip_headsign: r.trip_headsign,
    direction_id: r.direction_id,
  }));

  const stopTimes = rows<Record<string, string>>(files.get('stop_times.txt')!).map((r) => ({
    trip_id: r.trip_id,
    arrival_time: normalizeTime(r.arrival_time),
    departure_time: normalizeTime(r.departure_time),
    stop_id: r.stop_id,
    stop_sequence: Number.parseInt(r.stop_sequence, 10) || 0,
    stop_headsign: r.stop_headsign,
  }));

  // --- calendar.txt (opcional) ---
  const calendars = new Map<string, GtfsServiceCalendar>();
  const calendarContent = files.get('calendar.txt');
  if (calendarContent) {
    const rawCalendars = rows<Record<string, string>>(calendarContent);
    const lookup = new Map<string, GtfsServiceCalendar>();
    for (const r of rawCalendars) {
      const sid = r.service_id;
      const existing = lookup.get(sid) ?? {
        service_id: sid,
        days: [false, false, false, false, false, false, false] as GtfsServiceCalendar['days'],
        startDate: r.start_date,
        endDate: r.end_date,
      };
      DAY_NAMES.forEach((name, i) => {
        existing.days[i] = r[name] === '1';
      });
      if (r[DAY_NAMES[0]] !== undefined) lookup.set(sid, existing);
      calendars.set(sid, existing);
    }
  }

  // --- calendar_dates.txt (opcional) ---
  const calendarExceptions = new Map<string, GtfsCalendarDate[]>();
  const calendarDatesContent = files.get('calendar_dates.txt');
  if (calendarDatesContent) {
    for (const r of rows<Record<string, string>>(calendarDatesContent)) {
      const item: GtfsCalendarDate = {
        service_id: r.service_id,
        date: r.date,
        exception_type: r.exception_type === '1' ? 1 : 2,
      };
      const list = calendarExceptions.get(r.service_id) ?? [];
      list.push(item);
      calendarExceptions.set(r.service_id, list);
    }
  }

  return { stops, routes, trips, stopTimes, calendars, calendarExceptions };
}

/**
 * Distancia en km entre dos coordenadas (Haversine).
 */
export function haversineKm(
  a: { stop_lat?: number; stop_lon?: number },
  b: { stop_lat?: number; stop_lon?: number },
): number {
  if (a.stop_lat === undefined || a.stop_lon === undefined || b.stop_lat === undefined || b.stop_lon === undefined) {
    return 0;
  }
  const R = 6371;
  const dLat = ((b.stop_lat - a.stop_lat) * Math.PI) / 180;
  const dLon = ((b.stop_lon - a.stop_lon) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.stop_lat * Math.PI) / 180) * Math.cos((b.stop_lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

