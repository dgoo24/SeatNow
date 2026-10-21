import { z } from 'zod';

export const registerSchema = z.object({
  email: z.string().email('Ungültige E-Mail'),
  password: z.string().min(8, 'Das Passwort muss mindestens 8 Zeichen haben'),
  fullName: z.string().min(2, 'Vollständiger Name erforderlich'),
  phone: z.string().min(6).optional(),
});

export const loginSchema = z.object({
  email: z.string().email('Ungültige E-Mail'),
  password: z.string().min(1, 'Passwort erforderlich'),
});

export type RegisterInput = z.infer<typeof registerSchema>;
export type LoginInput = z.infer<typeof loginSchema>;
