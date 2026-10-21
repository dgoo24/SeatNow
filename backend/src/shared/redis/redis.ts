import Redis from 'ioredis';
import { env } from '../../config/env';

declare global {
  // eslint-disable-next-line no-var
  var __redis: Redis | undefined;
}

export const redis =
  global.__redis ??
  new Redis(env.REDIS_URL, {
    maxRetriesPerRequest: null,
    lazyConnect: false,
  });

redis.on('error', (err) => {
  // eslint-disable-next-line no-console
  console.error('[redis] error:', err.message);
});

if (env.NODE_ENV !== 'production') {
  global.__redis = redis;
}

export async function connectRedis(): Promise<void> {
  if (redis.status === 'ready' || redis.status === 'connecting') return;
  await redis.connect();
}

export async function disconnectRedis(): Promise<void> {
  await redis.quit();
}
