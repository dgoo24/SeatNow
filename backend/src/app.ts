import fs from 'node:fs';
import path from 'node:path';
import Fastify, { type FastifyInstance } from 'fastify';
import cors from '@fastify/cors';
import jwt from '@fastify/jwt';
import multipart from '@fastify/multipart';
import fastifyStatic from '@fastify/static';
import { env, uploadDir } from './config/env';
import { AppError } from './shared/errors';
import { authRoutes } from './modules/auth/auth.routes';
import { trainsRoutes } from './modules/trains/trains.routes';
import { bookingsRoutes } from './modules/bookings/bookings.routes';
import { adminRoutes } from './modules/admin/admin.routes';
import { demoRoutes } from './modules/demo/demo.routes';
import { paymentsMode } from './modules/payments/payments.service';
import { isMailEnabled } from './config/env';

export async function buildApp(): Promise<FastifyInstance> {
  const app = Fastify({
    logger: {
      level: env.NODE_ENV === 'development' ? 'info' : 'warn',
    },
    trustProxy: true,
  });

  await app.register(cors, {
    origin: env.NODE_ENV === 'development' ? true : [env.FRONTEND_URL],
    credentials: true,
  });

  await app.register(jwt, {
    secret: env.JWT_SECRET,
    sign: { expiresIn: env.JWT_EXPIRES_IN },
  });

  await app.register(multipart, {
    limits: { fileSize: 15 * 1024 * 1024 },
  });

  fs.mkdirSync(uploadDir, { recursive: true });
  await app.register(fastifyStatic, {
    root: uploadDir,
    prefix: '/uploads/',
    decorateReply: false,
  });

  // Interfaz web de demo: single-file en backend/public servido en / y /app.
  const publicDir = path.resolve(__dirname, '..', 'public');
  const indexHtmlPath = path.join(publicDir, 'index.html');
  // En produccion se cachea en memoria; en desarrollo se relee en cada peticion
  // para que los cambios en index.html se vean sin reiniciar el servidor.
  let cachedIndexHtml: Buffer | null = null;
  const getIndexHtml = async (): Promise<Buffer> => {
    if (env.NODE_ENV === 'production') {
      cachedIndexHtml ??= await fs.promises.readFile(indexHtmlPath);
      return cachedIndexHtml;
    }
    return fs.promises.readFile(indexHtmlPath);
  };
  const htmlCacheHeaders = env.NODE_ENV === 'production' ? {} : { 'Cache-Control': 'no-store' };
  app.get('/', async (_request, reply) => {
    reply.type('text/html; charset=utf-8').headers(htmlCacheHeaders).send(await getIndexHtml());
  });
  app.get('/app', async (_request, reply) => {
    reply.type('text/html; charset=utf-8').headers(htmlCacheHeaders).send(await getIndexHtml());
  });

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AppError) {
      return reply.code(error.statusCode).send({ error: error.code, message: error.message });
    }
    const statusCode = (error as { statusCode?: number }).statusCode;
    if (statusCode && statusCode < 500) {
      return reply.code(statusCode).send({ error: 'REQUEST_ERROR', message: error.message });
    }
    request.log.error(error);
    return reply.code(500).send({ error: 'INTERNAL_ERROR', message: 'Interner Serverfehler' });
  });

  app.setNotFoundHandler((request, reply) => {
    reply.code(404).send({ error: 'NOT_FOUND', message: `Ruta no encontrada: ${request.method} ${request.url}` });
  });

  app.get('/health', async () => ({
    status: 'ok',
    time: new Date().toISOString(),
    payments: paymentsMode(),
    mail: isMailEnabled ? 'smtp' : 'disabled',
  }));

  await app.register(authRoutes);
  await app.register(trainsRoutes);
  await app.register(bookingsRoutes);
  await app.register(adminRoutes);

  // Solo en entornos no productivos se exponen rutas de demo (compras simuladas).
  if (env.NODE_ENV !== 'production') {
    await app.register(demoRoutes);
  }

  return app;
}
