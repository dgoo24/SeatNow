import { redis } from './redis';
import { env } from '../../config/env';

/**
 * Clave de lock para un recurso concreto. En este MVP el recurso es un
 * asiento dentro de un tramo concreto: "asiento 42 del tramo A->B".
 * Esto permite reservas disjuntas del mismo asiento en tramos distintos.
 */
export const resourceLockKey = (resourceKey: string) => `lock:${resourceKey}`;
/** Clave de lock de un (asiento, tramo): la misma que usa SimulatedProvider. */
export const seatSegmentLockKey = (seatId: string, segmentId: string) =>
  resourceLockKey(`seat:${seatId}:seg:${segmentId}`);
/** Prefijo compartido para identificar locks de retencion en keyspace events. */
export const HOLD_LOCK_PREFIX = 'lock:seat:';
export const HOLD_LOCK_REGEX = /^lock:seat:(.+):seg:(.+)$/;
export const tripChannelKey = (tripId: string) => `live:trip:${tripId}`;

export interface ResourceLockInfo {
  userId: string;
  bookingId: string;
  ttlSeconds?: number;
}

/**
 * Intenta adquirir el bloqueo temporal de un recurso usando SET NX EX.
 * Devuelve true si el bloqueo fue adquirido, false si ya estaba bloqueado.
 */
export async function acquireResourceLock(
  resourceKey: string,
  info: ResourceLockInfo,
): Promise<boolean> {
  const ttl = info.ttlSeconds ?? env.SEAT_HOLD_TTL_SECONDS;
  const value = JSON.stringify({
    userId: info.userId,
    bookingId: info.bookingId,
    acquiredAt: new Date().toISOString(),
  });
  const result = await redis.set(resourceLockKey(resourceKey), value, 'EX', ttl, 'NX');
  return result === 'OK';
}

export async function getResourceLock(
  resourceKey: string,
): Promise<{ userId: string; bookingId: string } | null> {
  const raw = await redis.get(resourceLockKey(resourceKey));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as { userId: string; bookingId: string };
  } catch {
    return null;
  }
}

export async function getResourceLockTtl(resourceKey: string): Promise<number> {
  const ttl = await redis.ttl(resourceLockKey(resourceKey));
  return ttl > 0 ? ttl : 0;
}

/**
 * Libera el bloqueo solo si pertenece a la reserva indicada (via Lua atomico).
 */
export async function releaseResourceLock(
  resourceKey: string,
  bookingId?: string,
): Promise<boolean> {
  const key = resourceLockKey(resourceKey);
  if (!bookingId) {
    return (await redis.del(key)) > 0;
  }
  const lua = `
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local ok, data = pcall(cjson.decode, raw)
    if ok and data['bookingId'] == ARGV[1] then
      return redis.call('DEL', KEYS[1])
    end
    return 0
  `;
  const res = (await redis.eval(lua, 1, key, bookingId)) as number;
  return res > 0;
}

/**
 * Extiende el TTL del lock si sigue perteneciendo a la reserva indicada.
 */
export async function extendResourceLock(
  resourceKey: string,
  bookingId: string,
  ttlSeconds: number,
): Promise<boolean> {
  const key = resourceLockKey(resourceKey);
  const lua = `
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local ok, data = pcall(cjson.decode, raw)
    if ok and data['bookingId'] == ARGV[1] then
      return redis.call('EXPIRE', KEYS[1], ARGV[2])
    end
    return 0
  `;
  const res = (await redis.eval(lua, 1, key, bookingId, String(ttlSeconds))) as number;
  return res > 0;
}

/** Backward-compat helpers (lock por asiento, sin tramo). */
export const seatLockKey = (seatId: string) => resourceLockKey(`seat:${seatId}`);
export const acquireSeatLock = (info: { seatId: string } & ResourceLockInfo) =>
  acquireResourceLock(`seat:${info.seatId}`, info);
export const getSeatLockTtl = (seatId: string) => getResourceLockTtl(`seat:${seatId}`);
export const releaseSeatLock = (seatId: string, bookingId?: string) =>
  releaseResourceLock(`seat:${seatId}`, bookingId);
export const extendSeatLock = (seatId: string, bookingId: string, ttlSeconds: number) =>
  extendResourceLock(`seat:${seatId}`, bookingId, ttlSeconds);