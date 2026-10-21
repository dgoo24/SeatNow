import '@fastify/jwt';
import type { AuthUser } from '../shared/auth/middleware';

declare module '@fastify/jwt' {
  interface FastifyJWT {
    payload: AuthUser;
    user: AuthUser;
  }
}
