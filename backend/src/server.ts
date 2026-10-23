import { buildApp } from './app';
import { env } from './config/env';
import { connectDatabase, disconnectDatabase } from './shared/database/prisma';
import { connectRedis, disconnectRedis } from './shared/redis/redis';
import { startRedisKeyspaceListener, stopRedisKeyspaceListener } from './shared/redis/keyspace';
import { sseBroker } from './shared/sse/sse-broker';
import { sweepExpiredHolds } from './modules/bookings/bookings.service';
import { sweepNoShowBookings } from './modules/bookings/checkin.service';
import { onRedisKeyExpired } from './modules/inventory/hold-expiry';
import { startRealtimePoller } from './modules/realtime';
import { warmStopNamesCache } from './modules/trains/trains.service';

async function main(): Promise<void> {
  await connectDatabase();
  // eslint-disable-next-line no-console
  console.log('[db] Conectado a PostgreSQL');
  warmStopNamesCache();

  try {
    await connectRedis();
    // eslint-disable-next-line no-console
    console.log('[redis] Conectado a Redis');
    // Expiracion natural de holds (TTL) -> liberacion inmediata via SSE.
    startRedisKeyspaceListener((key) => {
      void onRedisKeyExpired(key).catch(() => undefined);
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[redis] No se pudo conectar:', (err as Error).message);
  }

  const app = await buildApp();

  const sweepTimer = setInterval(() => {
    sweepExpiredHolds()
      .then((n) => {
        if (n > 0) app.log.info(`[bookings] ${n} reservas expiradas liberadas`);
      })
      .catch((err) => app.log.error(err, '[bookings] Error en el barrido de reservas'));
  }, 30_000);

  // Regla No-Show (Comfort Check-In): libera asientos sin check-in tras la salida.
  const noShowTimer = setInterval(() => {
    sweepNoShowBookings()
      .then((r) => {
        if (r.released.length > 0) {
          app.log.info(`[no-show] ${r.released.length} reservas liberadas por No-Show`);
        }
      })
      .catch((err) => app.log.error(err, '[no-show] Error en el barrido No-Show'));
  }, env.NO_SHOW_SWEEP_MS);

  const pingTimer = setInterval(() => sseBroker.pingAll(), 20_000);
  pingTimer.unref?.();
  sweepTimer.unref?.();
  noShowTimer.unref?.();
  const realtimeTimer = startRealtimePoller();
  realtimeTimer.unref?.();

  await app.listen({ port: env.PORT, host: '0.0.0.0' });
  // eslint-disable-next-line no-console
  console.log(`[api] Escuchando en http://localhost:${env.PORT}`);

  const shutdown = async (signal: string): Promise<void> => {
    // eslint-disable-next-line no-console
    console.log(`\n[api] Cerrando por ${signal}...`);
    clearInterval(sweepTimer);
    clearInterval(noShowTimer);
    clearInterval(pingTimer);
    clearInterval(realtimeTimer);
    await app.close();
    await stopRedisKeyspaceListener().catch(() => undefined);
    await disconnectDatabase().catch(() => undefined);
    await disconnectRedis().catch(() => undefined);
    process.exit(0);
  };

  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error('[api] Error fatal al iniciar:', err);
  process.exit(1);
});
