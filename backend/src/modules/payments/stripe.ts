import Stripe from 'stripe';
import { env, isStripeEnabled } from '../../config/env';

let client: Stripe | null = null;

export function getStripe(): Stripe | null {
  if (!isStripeEnabled) return null;
  if (!client) {
    client = new Stripe(env.STRIPE_SECRET_KEY, { typescript: true });
  }
  return client;
}

export { isStripeEnabled };
