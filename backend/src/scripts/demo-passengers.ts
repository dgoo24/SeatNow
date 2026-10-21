import { disconnectDatabase } from '../shared/database/prisma';
import { simulatePurchase } from '../modules/demo/demo.service';

/**
 * Simula pasajeros reales comprando su reserva "a mitad de trayecto"
 * reutilizando la logica del endpoint demo (simulatePurchase).
 * Ejecuta HOLD -> CHECKOUT igual que la app movil para ver el mapa del
 * vagon actualizarse en vivo (SSE seat:update).
 *
 * Uso: npm run demo:passengers -- --iterations 6
 */
function parseIterations(): number {
  const idx = process.argv.indexOf('--iterations');
  if (idx === -1 || !process.argv[idx + 1]) return 6;
  const value = Number.parseInt(process.argv[idx + 1], 10);
  return Number.isNaN(value) ? 6 : Math.max(1, value);
}

const ITERATIONS = parseIterations();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function run(): Promise<void> {
  // eslint-disable-next-line no-console
  console.log(`[demo] Simulando ${ITERATIONS} compras en vivo (hot del inventario)...`);

  let purchased = 0;
  for (let i = 0; i < ITERATIONS; i += 1) {
    const result = await simulatePurchase();
    purchased += 1;
    // eslint-disable-next-line no-console
    console.log(
      `[demo] #${i + 1} ${result.passengerEmail} compro asiento ${result.carNumber}-${result.seatNumber} ` +
        `en ${result.segmentFrom} -> ${result.segmentTo} (booking ${result.bookingId.slice(0, 8)})`,
    );
    const delay = 2500 + Math.floor(Math.random() * 5000);
    await sleep(delay);
  }

  // eslint-disable-next-line no-console
  console.log(`[demo] Compras simuladas: ${purchased}`);
  await disconnectDatabase();
  process.exit(0);
}

run().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error('[demo] Error:', err);
  await disconnectDatabase();
  process.exit(1);
});