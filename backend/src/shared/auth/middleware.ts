import type { FastifyReply, FastifyRequest } from 'fastify';
import { ForbiddenError, UnauthorizedError } from '../errors';

export interface AuthUser {
  sub: string;
  email: string;
  role: 'USER' | 'ADMIN';
}

export async function authenticate(request: FastifyRequest, _reply: FastifyReply): Promise<void> {
  try {
    await request.jwtVerify();
  } catch {
    throw new UnauthorizedError('Zugriffstoken ungültig oder fehlt');
  }
}

export async function requireAdmin(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  await authenticate(request, reply);
  const user = request.user as AuthUser;
  if (user.role !== 'ADMIN') {
    throw new ForbiddenError('Administratorrolle erforderlich');
  }
}

export function currentUser(request: FastifyRequest): AuthUser {
  return request.user as AuthUser;
}
