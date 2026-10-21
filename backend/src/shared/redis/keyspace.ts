/**
 * Suscripcion a las keyspace notifications de Redis
 * (`__keyevent@<db>__:expired`) para reaccionar al instante a la expiracion
 * natural del TTL de un hold de asiento (lock `lock:seat:{seatId}:seg:{segmentId}`).
 *
 * Sin esto, el frontend solo se entera del release cuando el barrido periodico
 * (sweepExpiredHolds, cada 30 s) reconcilia la DB. Con keyspace events el
 * asiento vuelve a AVAILABLE (SSE) en el momento exacto en que Redis expira.
 *
 * Requiere en el servidor Redis: `notify-keyspace-events Ex` (configurado en
 * docker-compose y reintentado aqui con CONFIG SET en el arranque).
 */

import Redis from 'ioredis';
import { env } from '../../config/env';
import { redis } from './redis';

let subscriber: Redis | null = null;
let started = false;

/** Indice de DB del REDIS_URL (por defecto 0). */
function dbIndexFromUrl(): number {
  try {
    const path = new URL(env.REDIS_URL).pathname;
    const n = Number.parseInt(path.replace(/\//, ''), 10);
    return Number.isNaN(n) ? 0 : n;
  } catch {
    return 0;
  }
}

export function startRedisKeyspaceListener(onExpired: (key: string) => void): void {
  if (started) return;
  started = true;

  // Best-effort: si el servidor no permite CONFIG SET (algunos managed),
  // las notificaciones pueden seguir desactivadas y el barrido sigue siendo
  // el fallback (duplicado de seguridad).
  void redis
    .config('SET', 'notify-keyspace-events', 'Ex')
    .then(() => {
      // eslint-disable-next-line no-console
      console.log('[redis] notify-keyspace-events=Ex activado');
    })
    .catch((err: Error) => {
      // eslint-disable-next-line no-console
      console.warn('[redis] No se pudo activar notify-keyspace-events:', err.message);
    });

  const channel = `__keyevent@${dbIndexFromUrl()}__:expired`;
  subscriber = new Redis(env.REDIS_URL, {
    maxRetriesPerRequest: null,
    lazyConnect: true,
    enableReadyCheck: false,
    connectionName: 'keyspace-expired',
  });

  subscriber.on('error', (err: Error) => {
    // eslint-disable-next-line no-console
    console.error('[redis:keyspace] error:', err.message);
  });

  subscriber.subscribe(channel, (err) => {
    if (err) {
      // eslint-disable-next-line no-console
      console.error('[redis:keyspace] No se pudo suscribir a', channel, err.message);
      return;
    }
    // eslint-disable-next-line no-console
    console.log(`[redis:keyspace] Escuchando expiraciones en ${channel}`);
  });

  subscriber.on('message', (_channel: string, key: string) => {
    try {
      onExpired(key);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('[redis:keyspace] Error manejando clave expirada:', (err as Error).message);
    }
  });
}

export async function stopRedisKeyspaceListener(): Promise<void> {
  if (!subscriber) return;
  const s = subscriber;
  subscriber = null;
  started = false;
  await s.quit().catch(() => undefined);
}
