import { randomUUID } from 'node:crypto';
import { prisma, disconnectDatabase } from '../shared/database/prisma';
import { hashPassword } from '../shared/auth/password';
import { intervalsOverlap } from '../modules/inventory';

const DEMO_EMAIL = process.env.DEMO_EMAIL ?? 'demo@trenes.local';

/**
 * Prepara el mapa de asientos de los viajes que circulan HOY (dato real del GTFS):
 * crea ocupacion CONFIRMED realista en asientos y tramos disjuntos para que el
 * demo del vagon por Teilstrecken se vea "vivo" desde el arranque.
 *
 * Uso: npm run demo:occupancy -- --trips 12 --occupancy 0.25
 */
async function ensureDemoUser(): Promise<string> {
  const passwordHash = await hashPassword('demo1234');
  const user = await prisma.user.upsert({
    where: { email: DEMO_EMAIL },
    create: {
      email: DEMO_EMAIL,
      passwordHash,
      fullName: 'Pasajero Demo',
      phone: '+34 600 000 000',
      role: 'USER',
    },
    update: {},
  });
  return user.id;
}

function parseArg(name: string, fallback: number): number {
  const idx = process.argv.indexOf(name);
  if (idx === -1 || !process.argv[idx + 1]) return fallback;
  const value = Number.parseFloat(process.argv[idx + 1]);
  return Number.isNaN(value) ? fallback : value;
}

async function historyForTrip(tripId: string) {
  const existing = await prisma.booking.findMany({
    where: { tripId, status: { in: ['PENDING_PAYMENT', 'CONFIRMED'] } },
    include: { segment: true },
  });
  const used = new Map<string, { from: number; to: number }[]>(); // seatId -> [segmentos activos]
  for (const b of existing) {
    const list = used.get(b.seatId) ?? [];
    list.push({ from: b.segment.originSequence, to: b.segment.destinationSequence });
    used.set(b.seatId, list);
  }
  return used;
}

async function seedTrip(tripId: string, capacity: number, userId: string): Promise<number> {
  const [seats, segments, used] = await Promise.all([
    prisma.seat.findMany({ where: { tripId } }),
    prisma.segment.findMany({
      where: { tripId },
      orderBy: [{ originSequence: 'asc' }, { destinationSequence: 'asc' }],
    }),
    historyForTrip(tripId),
  ]);
  if (seats.length === 0 || segments.length === 0) return 0;

  const count = Math.max(1, Math.round(seats.length * capacity));
  let created = 0;

  for (let i = 0; i < count; i += 1) {
    const seat = seats[Math.floor(Math.random() * seats.length)];
    const safeSegments = segments.filter(
      (s) => !(used.get(seat.id) ?? []).some((iv) =>
        intervalsOverlap({ from: s.originSequence, to: s.destinationSequence }, iv),
      ),
    );
    if (safeSegments.length === 0) continue;

    // Simula pasajeros que bajan/suben en distintas paradas: elige 1-3 tramos
    // disjuntos por asiento (Teilstrecken).
    const chosen = safeSegments[Math.floor(Math.random() * safeSegments.length)];
    await prisma.booking.create({
      data: {
        userId,
        tripId,
        seatId: seat.id,
        segmentId: chosen.id,
        status: 'CONFIRMED',
        qrPayload: `TKT-${randomUUID().replace(/-/g, '').slice(0, 16).toUpperCase()}`,
        totalAmount: chosen.price,
        expiresAt: null,
      },
    });
    used.set(seat.id, [...(used.get(seat.id) ?? []), {
      from: chosen.originSequence,
      to: chosen.destinationSequence,
    }]);
    created += 1;
  }

  return created;
}

async function main(): Promise<void> {
  const tripLimit = Math.min(Math.max(parseArg('--trips', 12), 1), 50);
  const capacity = Math.min(Math.max(parseArg('--occupancy', 0.25), 0.05), 0.9);

  const userId = await ensureDemoUser();

  const today = new Date();
  const y = today.getUTCFullYear();
  const m = String(today.getUTCMonth() + 1).padStart(2, '0');
  const d = String(today.getUTCDate()).padStart(2, '0');
  const start = new Date(`${y}-${m}-${d}T00:00:00.000Z`);
  const end = new Date(`${y}-${m}-${d}T23:59:59.999Z`);

  const activeDates = await prisma.tripServiceDate.findMany({
    where: { serviceDate: { gte: start, lte: end } },
    select: { tripId: true, serviceDate: true },
    orderBy: { serviceDate: 'asc' },
    take: tripLimit * 2,
  });
  const byTrip = new Map<string, string>();
  for (const d of activeDates) byTrip.set(d.tripId, d.serviceDate.toISOString().slice(0, 10));
  const tripIds = [...byTrip.keys()].slice(0, tripLimit);

  // eslint-disable-next-line no-console
  console.log(`[demo] Viajes activos hoy: ${tripIds.length}`);
  let total = 0;
  for (const tripId of tripIds) {
    const created = await seedTrip(tripId, capacity, userId);
    total += created;
    // eslint-disable-next-line no-console
    console.log(`[demo] trip ${tripId} -> ${created} asientos ocupados`);
  }

  // eslint-disable-next-line no-console
  console.log(`[demo] Ocupacion total creada: ${total} reservas CONFIRMED (asiento + tramo)`);
  await disconnectDatabase();
  process.exit(0);
}

main().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error('[demo] Error:', err);
  await disconnectDatabase();
  process.exit(1);
});