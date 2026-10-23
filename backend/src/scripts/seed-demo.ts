import { prisma, disconnectDatabase } from '../shared/database/prisma';
import { hashPassword } from '../shared/auth/password';

const DEMO_EMAIL = process.env.DEMO_EMAIL ?? 'demo@trenes.local';
const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? 'demo1234';

async function main(): Promise<void> {
  const passwordHash = await hashPassword(DEMO_PASSWORD);
  const user = await prisma.user.upsert({
    where: { email: DEMO_EMAIL },
    create: {
      email: DEMO_EMAIL,
      passwordHash,
      fullName: 'Demo Passagier',
      phone: '+49 600 000 000',
      role: 'USER',
    },
    update: {},
  });
  // eslint-disable-next-line no-console
  console.log(`[seed] Usuario demo listo: ${user.email}`);
  await disconnectDatabase();
}

main().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error('[seed] Error:', err);
  await disconnectDatabase();
  process.exit(1);
});
