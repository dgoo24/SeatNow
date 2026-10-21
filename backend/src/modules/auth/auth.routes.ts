import type { FastifyInstance } from 'fastify';
import { authenticate, currentUser } from '../../shared/auth/middleware';
import { parseOrThrow } from '../../shared/validation';
import { loginSchema, registerSchema } from './auth.schemas';
import { authenticateUser, getProfile, registerUser } from './auth.service';

export async function authRoutes(app: FastifyInstance): Promise<void> {
  app.post('/api/auth/register', async (request, reply) => {
    const input = parseOrThrow(registerSchema, request.body);
    const user = await registerUser(input);
    const token = app.jwt.sign({ sub: user.id, email: user.email, role: user.role });
    return reply.code(201).send({ user, token });
  });

  app.post('/api/auth/login', async (request, reply) => {
    const input = parseOrThrow(loginSchema, request.body);
    const user = await authenticateUser(input);
    const token = app.jwt.sign({ sub: user.id, email: user.email, role: user.role });
    return reply.send({ user, token });
  });

  app.get('/api/auth/me', { preHandler: authenticate }, async (request) => {
    return getProfile(currentUser(request).sub);
  });
}
