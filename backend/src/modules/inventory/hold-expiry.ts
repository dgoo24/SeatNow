/**
 * Reaccion a la expiracion natural del TTL de un hold en Redis
 * (keyspace notification `__keyevent@*__:expired`).
 *
 * Cuando muere `lock:seat:{seatId}:seg:{segmentId}` sin que se haya confirmado
 * el pago, la reserva PENDING_PAYMENT asociada se marca FAILED al instante y el
 * asiento vuelve a AVAILABLE en el mapa del vagon via SSE (evento seat:update
 * y hold:expired), sin esperar al barrido periodico de sweepExpiredHolds.
 */

import { prisma } from '../../shared/database/prisma';
import { redis } from '../../shared/redis/redis';
import { HOLD_LOCK_REGEX } from '../../shared/redis/locks';
import { sseBroker } from '../../shared/sse/sse-broker';

const HOLD_TTL_FALLBACK_MS = 5 * 60 * 1000;

/** Handler de una clave expirada (cualquier clave; filtra las de hold). */
export async function onRedisKeyExpired(key: string): Promise<void> {
  const match = HOLD_LOCK_REGEX.exec(key);
  if (!match) return;

  const [, seatId, segmentId] = match;
  if (!seatId || !segmentId) return;

  // Guarda anti-falsa-positiva: si la clave fue re-adquirida por un nuevo hold
  // (SET NX tras expirar), no liberar nada.
  const reacquired = await redis.get(key).catch(() => null);
  if (reacquired) return;

  const segment = await prisma.segment.findUnique({ where: { id: segmentId } });
  if (!segment) return;

  // Transicion atomica: solo si sigue PENDING_PAYMENT (evita carreras con
  // checkout, cancelacion o el sweep periodico).
  const stale = await prisma.booking.findFirst({
    where: { seatId, segmentId, status: 'PENDING_PAYMENT' },
    select: { id: true, userId: true },
  });
  if (!stale) return;

  const updated = await prisma.booking.updateMany({
    where: { id: stale.id, status: 'PENDING_PAYMENT' },
    data: {
      status: 'FAILED',
      failureReason: 'Zahlungsfrist abgelaufen (Redis-Hold-TTL)',
      expiresAt: null,
    },
  });
  if (updated.count === 0) return;

  const seat = await prisma.seat.findUnique({ where: { id: seatId }, select: { seatNumber: true } });
  const at = new Date().toISOString();

  sseBroker.broadcastToTrip(segment.tripId, 'seat:update', {
    seatId,
    segmentId,
    status: 'AVAILABLE',
    bookingId: stale.id,
    seatNumber: seat?.seatNumber,
    segmentFrom: segment.originStopName,
    segmentTo: segment.destinationStopName,
    reason: 'hold_expired',
    at,
  });
  sseBroker.broadcastToTrip(segment.tripId, 'hold:expired', {
    seatId,
    segmentId,
    bookingId: stale.id,
    seatNumber: seat?.seatNumber,
    ttlMs: HOLD_TTL_FALLBACK_MS,
    at,
  });
  sseBroker.sendToUser(segment.tripId, stale.userId, 'booking:update', {
    bookingId: stale.id,
    status: 'FAILED',
    message: 'Die Reservierungsfrist ist abgelaufen. Der Sitzplatz wurde wieder freigegeben.',
  });
}
