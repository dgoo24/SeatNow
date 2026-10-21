import { env } from '../../config/env';
import { DbProvider } from './db';
import { SimulatedProvider } from './simulated';
import type { InventoryProvider } from './types';

let instance: InventoryProvider | null = null;

/** Devuelve el proveedor de inventario activo segun INVENTORY_PROVIDER. */
export function inventoryProvider(): InventoryProvider {
  if (!instance) {
    instance = env.INVENTORY_PROVIDER === 'db' ? new DbProvider() : new SimulatedProvider();
  }
  return instance;
}

export { intervalsOverlap } from './simulated';
export * from './types';