import path from 'node:path';
import dotenv from 'dotenv';
import { z } from 'zod';

dotenv.config();
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

const booleanish = z
  .union([z.string(), z.boolean()])
  .transform((v) => (typeof v === 'boolean' ? v : v.toLowerCase() === 'true'));

const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(3000),
  FRONTEND_URL: z.string().default('http://localhost:19006'),

  DATABASE_URL: z.string().min(1, 'DATABASE_URL es obligatorio'),
  REDIS_URL: z.string().min(1, 'REDIS_URL es obligatorio'),

  JWT_SECRET: z.string().min(8, 'JWT_SECRET debe tener al menos 8 caracteres'),
  JWT_EXPIRES_IN: z.string().default('7d'),
  SEAT_HOLD_TTL_SECONDS: z.coerce.number().int().positive().default(300),

  STRIPE_SECRET_KEY: z.string().optional().default(''),
  STRIPE_PUBLISHABLE_KEY: z.string().optional().default(''),
  STRIPE_WEBHOOK_SECRET: z.string().optional().default(''),

  SMTP_HOST: z.string().default('smtp.mailtrap.io'),
  SMTP_PORT: z.coerce.number().int().default(587),
  SMTP_SECURE: booleanish.default(false),
  SMTP_USER: z.string().optional().default(''),
  SMTP_PASS: z.string().optional().default(''),
  MAIL_FROM: z.string().default('Trenes MVP <no-reply@trenes-mvp.local>'),

  GTFS_SOURCE: z.string().default('./gtfs-data'),
  GTFS_URL: z.string().optional().default(''),
  GTFS_FILTER_STOPS: z.string().optional().default(''),

  INVENTORY_PROVIDER: z.enum(['simulated', 'db']).default('simulated'),

  // Tiempo real de los trenes (retrasos, vias, cancelaciones) vs. horarios GTFS.
  // "db-rest": espejo publico de los datos reales de DB (v6/v5 db.transport.rest). "off": desactivado.
  REALTIME_PROVIDER: z.enum(['bahn-web', 'db-rest', 'off']).default('bahn-web'),
  // EVA (codigo de estacion DB) de la parada de referencia para el tablero en vivo (Fulda = 8000115)
  REALTIME_FULDA_EVA: z.string().min(1).default('8000115'),
  // URL base opcional para pruebas/desarrollo (p. ej. un espejo local). Vacia = hosts publicos.
  REALTIME_DB_REST_URL: z.string().optional().default(''),
  // Cadencia del empuje en vivo (SSE) del tiempo real: cada cuantos ms se
  // re-consulta Fulda para los viajes con pagina abierta. Minimo 10 s.
  REALTIME_POLL_MS: z.coerce.number().int().positive().default(20000),

  // --- Regla No-Show (Comfort Check-In): liberar el asiento si el pasajero
  // no hace check-in en los primeros N minutos tras la salida de su origen.
  NO_SHOW_GRACE_MINUTES: z.coerce.number().int().nonnegative().default(15),
  NO_SHOW_SWEEP_MS: z.coerce.number().int().positive().default(60_000),

  // --- Disrupciones: retraso critico (min) que notifica/provoca reasignacion.
  CRITICAL_DELAY_MINUTES: z.coerce.number().int().positive().default(60),

  UPLOAD_DIR: z.string().default('./uploads'),
  PUBLIC_BASE_URL: z.string().default('http://localhost:3000'),
});

export type Env = z.infer<typeof envSchema>;

const parsed = envSchema.safeParse(process.env);

if (!parsed.success) {
  const details = parsed.error.issues
    .map((i) => `  - ${i.path.join('.')}: ${i.message}`)
    .join('\n');
  // eslint-disable-next-line no-console
  console.error(`\n[env] Variables de entorno invalidas:\n${details}\n`);
  process.exit(1);
}

export const env = parsed.data;

export const isStripeEnabled = env.STRIPE_SECRET_KEY.trim().length > 0;
export const isMailEnabled = env.SMTP_USER.trim().length > 0 && env.SMTP_PASS.trim().length > 0;
export const uploadDir = path.resolve(process.cwd(), env.UPLOAD_DIR);
