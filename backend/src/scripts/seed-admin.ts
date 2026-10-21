import { prisma, disconnectDatabase } from '../shared/database/prisma';
import { hashPassword } from '../shared/auth/password';

const ADMIN_EMAIL = process.env.ADMIN_EMAIL ?? 'admin@trenes.local';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? 'admin1234';

async function main(): Promise<void> {
  const passwordHash = await hashPassword(ADMIN_PASSWORD);
  const admin = await prisma.user.upsert({
    where: { email: ADMIN_EMAIL },
    create: {
      email: ADMIN_EMAIL,
      passwordHash,
      fullName: 'Concierge Admin',
      role: 'ADMIN',
    },
    update: { role: 'ADMIN', passwordHash },
  });
  // eslint-disable-next-line no-console
  console.log(`[seed] Admin listo: ${admin.email} (password: ${ADMIN_PASSWORD})`);
  await disconnectDatabase();
}

main().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error('[seed] Error:', err);
  await disconnectDatabase();
  process.exit(1);
});
