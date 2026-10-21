import { prisma } from '../../shared/database/prisma';
import { hashPassword } from '../../shared/auth/password';
import { ConflictError } from '../../shared/errors';
import { berlinDateStr, berlinDayUtcRange, berlinNowMinutes } from '../../shared/time/berlin';
import { inventoryProvider } from '../inventory';

const PASSENGERS = 10;

/**
 * Crea/recupera usuarios simulados (pasajeroN@trenes.local / demo1234)
 * usados tanto por la API demo como por el script demo-passengers.
 */
export async function ensurePassengerUsers(): Promise<{ id: string; email: string }[]> {
  const passwordHash = await hashPassword('demo1234');
  const users: { id: string; email: string }[] = [];
  for (let i = 1; i <= PASSENGERS; i += 1) {
    const email = `pasajero${i}@trenes.local`;
    const user = await prisma.user.upsert({
      where: { email },
      create: { email, passwordHash, fullName: `Pasajero Simulado ${i}`, role: 'USER' },
      update: {},
    });
    users.push({ id: user.id, email: user.email });
  }
  return users;
}

async function pickActiveTrip(): Promise<{ tripId: string; segmentId: string } | null> {
  const today = new Date();
  // Limite UTC de la fecha local (evitar colar dia anterior como el fix global)
  const y = today.getUTCFullYear();
  const m = String(today.getUTCMonth() + 1).padStart(2, '0');
  const d = String(today.getUTCDate()).padStart(2, '0');
  const start = new Date(`${y}-${m}-${d}T00:00:00.000Z`);
  const end = new Date(`${y}-${m}-${d}T23:59:59.999Z`);
  const active = await prisma.tripServiceDate.findMany({
    where: { serviceDate: { gte: start, lte: end } },
    select: { tripId: true },
    orderBy: { serviceDate: 'asc' },
    take: 30,
  });
  const tripIds = [...new Set(active.map((a) => a.tripId))];
  if (tripIds.length === 0) return null;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const tripId = tripIds[Math.floor(Math.random() * tripIds.length)];
    const segments = await prisma.segment.findMany({
      where: { tripId, durationMinutes: { gte: 60 } },
      orderBy: { durationMinutes: 'desc' },
      take: 6,
    });
    if (segments.length === 0) continue;
    const segment = segments[Math.floor(Math.random() * segments.length)];
    return { tripId, segmentId: segment.id };
  }
  return null;
}

export interface SimulatedPurchaseResult {
  tripId: string;
  segmentId: string;
  segmentFrom: string;
  segmentTo: string;
  seatNumber: string;
  carNumber: number;
  price: number;
  bookingId: string;
  passengerEmail: string;
}

/**
 * Simula un pasajero real comprando su reserva a mitad de trayecto:
 * elige un viaje activo hoy, un asiento libre en un tramo largo y
 * ejecuta HOLD -> CONFIRM igual que la app movil.
 */
export interface SimulatePurchaseTarget {
  tripId?: string;
  segmentId?: string;
}

export async function simulatePurchase(target?: SimulatePurchaseTarget): Promise<SimulatedPurchaseResult> {
  const provider = inventoryProvider();
  const passengers = await ensurePassengerUsers();

  let resolved: { tripId: string; segmentId: string } | null = null;
  if (target?.tripId && target?.segmentId) {
    const segment = await prisma.segment.findFirst({
      where: { id: target.segmentId, tripId: target.tripId },
      select: { id: true, tripId: true },
    });
    if (segment) resolved = { tripId: segment.tripId, segmentId: segment.id };
  }
  if (!resolved) resolved = await pickActiveTrip();
  if (!resolved) throw new ConflictError('Keine aktiven Fahrten heute zum Simulieren');
  const target2 = resolved;

  const availability = await provider.getSegmentAvailability(target2.segmentId);
  const freeSeats = availability.filter((a) => a.status === 'AVAILABLE');
  if (freeSeats.length === 0) throw new ConflictError('Keine freien Sitzplätze in dieser Teilstrecke');

  const seat = freeSeats[Math.floor(Math.random() * freeSeats.length)];
  const passenger = passengers[Math.floor(Math.random() * passengers.length)];

  const held = await provider.holdSeat({
    userId: passenger.id,
    tripId: target2.tripId,
    seatId: seat.seatId,
    segmentId: target2.segmentId,
    ttlSeconds: 90,
  });
  const confirmed = await provider.confirmBooking(held.booking.id);

  return {
    tripId: target2.tripId,
    segmentId: target2.segmentId,
    segmentFrom: confirmed.segment.originStopName,
    segmentTo: confirmed.segment.destinationStopName,
    seatNumber: confirmed.seat.seatNumber,
    carNumber: confirmed.seat.carNumber,
    price: Number(confirmed.segment.price),
    bookingId: confirmed.id,
    passengerEmail: passenger.email,
  };
}

export interface NoShowSeedResult {
  bookingId: string;
  tripId: string;
  segmentId: string;
  departureTime: string;
  seatNumber: string;
  passengerEmail: string;
}

/**
 * Crea una reserva CONFIRMED en un tramo cuya salida de hoy ya paso hace
 * 16-300 minutos (sin check-in): sirve para probar el barrido No-Show
 * sin esperar a la salida real del tren.
 */
export async function seedNoShowBooking(): Promise<NoShowSeedResult> {
  const provider = inventoryProvider();
  const passengers = await ensurePassengerUsers();

  const today = berlinDateStr();
  const { start, end } = berlinDayUtcRange(today);
  const nowMin = berlinNowMinutes();

  const active = await prisma.tripServiceDate.findMany({
    where: { serviceDate: { gte: start, lte: end } },
    select: { tripId: true },
  });
  const tripIds = [...new Set(active.map((a) => a.tripId))];
  if (tripIds.length === 0) throw new ConflictError('Keine aktiven Fahrten heute');

  const sample = [...tripIds].sort(() => Math.random() - 0.5).slice(0, 60);
  const segments = await prisma.segment.findMany({
    where: { tripId: { in: sample } },
    take: 600,
  });

  const departed = segments.filter((s) => {
    const [h, m] = s.departureTime.split(':').map((v) => Number.parseInt(v, 10) || 0);
    const ago = nowMin - (h * 60 + m);
    return ago >= 16 && ago <= 300;
  });
  if (departed.length === 0) {
    throw new ConflictError('Keine abgefahrenen Teilstrecken im Fenster 16-300 Min. – später erneut versuchen');
  }

  const segment = departed[Math.floor(Math.random() * departed.length)];
  const availability = await provider.getSegmentAvailability(segment.id);
  const free = availability.filter((a) => a.status === 'AVAILABLE');
  if (free.length === 0) throw new ConflictError('Keine freien Sitzplätze in dieser Teilstrecke');

  const seat = free[Math.floor(Math.random() * free.length)];
  const passenger = passengers[Math.floor(Math.random() * passengers.length)];

  const held = await provider.holdSeat({
    userId: passenger.id,
    tripId: segment.tripId,
    seatId: seat.seatId,
    segmentId: segment.id,
    ttlSeconds: 900,
  });
  const confirmed = await provider.confirmBooking(held.booking.id);

  return {
    bookingId: confirmed.id,
    tripId: segment.tripId,
    segmentId: segment.id,
    departureTime: segment.departureTime,
    seatNumber: confirmed.seat.seatNumber,
    passengerEmail: passenger.email,
  };
}