import { prisma } from '../../shared/database/prisma';
import { hashPassword, verifyPassword } from '../../shared/auth/password';
import { ConflictError, UnauthorizedError } from '../../shared/errors';
import type { LoginInput, RegisterInput } from './auth.schemas';

export interface PublicUser {
  id: string;
  email: string;
  fullName: string;
  phone: string | null;
  role: 'USER' | 'ADMIN';
  createdAt: Date;
}

function toPublic(user: {
  id: string;
  email: string;
  fullName: string;
  phone: string | null;
  role: 'USER' | 'ADMIN';
  createdAt: Date;
}): PublicUser {
  return {
    id: user.id,
    email: user.email,
    fullName: user.fullName,
    phone: user.phone,
    role: user.role,
    createdAt: user.createdAt,
  };
}

export async function registerUser(input: RegisterInput): Promise<PublicUser> {
  const existing = await prisma.user.findUnique({ where: { email: input.email.toLowerCase() } });
  if (existing) throw new ConflictError('Es existiert bereits ein Konto mit dieser E-Mail');

  const user = await prisma.user.create({
    data: {
      email: input.email.toLowerCase(),
      passwordHash: await hashPassword(input.password),
      fullName: input.fullName,
      phone: input.phone ?? null,
    },
  });
  return toPublic(user);
}

export async function authenticateUser(input: LoginInput): Promise<PublicUser> {
  const user = await prisma.user.findUnique({ where: { email: input.email.toLowerCase() } });
  if (!user) throw new UnauthorizedError('Ungültige Anmeldedaten');

  const ok = await verifyPassword(input.password, user.passwordHash);
  if (!ok) throw new UnauthorizedError('Ungültige Anmeldedaten');

  return toPublic(user);
}

export async function getProfile(userId: string): Promise<PublicUser> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user) throw new UnauthorizedError('Benutzer nicht gefunden');
  return toPublic(user);
}
