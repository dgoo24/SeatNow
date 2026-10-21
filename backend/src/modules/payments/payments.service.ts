import { randomUUID } from 'node:crypto';
import { getStripe, isStripeEnabled } from './stripe';

export interface ManualCaptureIntentResult {
  id: string;
  clientSecret: string | null;
  status: string;
  amount: number;
  currency: string;
  mock: boolean;
}

export interface CreateIntentInput {
  amount: number; // en la moneda base (EUR), se convierte a centimos
  currency?: string;
  paymentMethodId?: string;
  description?: string;
  metadata?: Record<string, string>;
}

const CURRENCY = 'eur';

/**
 * Crea un PaymentIntent con capture_method = 'manual' para retener el saldo
 * en la tarjeta SIN efectuar el cobro (pre-autorizacion).
 */
export async function createManualCaptureIntent(
  input: CreateIntentInput,
): Promise<ManualCaptureIntentResult> {
  const currency = input.currency ?? CURRENCY;
  const stripe = getStripe();

  if (!stripe) {
    return {
      id: `pi_mock_${randomUUID()}`,
      clientSecret: `pi_mock_secret_${randomUUID()}`,
      status: 'requires_capture',
      amount: input.amount,
      currency,
      mock: true,
    };
  }

  const intent = await stripe.paymentIntents.create({
    amount: Math.round(input.amount * 100),
    currency,
    capture_method: 'manual',
    description: input.description,
    metadata: input.metadata,
    ...(input.paymentMethodId
      ? {
          payment_method: input.paymentMethodId,
          confirm: true,
          automatic_payment_methods: { enabled: true, allow_redirects: 'never' as const },
        }
      : {}),
  });

  return {
    id: intent.id,
    clientSecret: intent.client_secret,
    status: intent.status,
    amount: intent.amount / 100,
    currency: intent.currency,
    mock: false,
  };
}

export async function captureIntent(paymentIntentId: string): Promise<{ status: string; mock: boolean }> {
  const stripe = getStripe();
  if (!stripe || paymentIntentId.startsWith('pi_mock_')) {
    return { status: 'succeeded', mock: true };
  }
  const intent = await stripe.paymentIntents.capture(paymentIntentId);
  return { status: intent.status, mock: false };
}

export async function cancelIntent(paymentIntentId: string): Promise<{ status: string; mock: boolean }> {
  const stripe = getStripe();
  if (!stripe || paymentIntentId.startsWith('pi_mock_')) {
    return { status: 'canceled', mock: true };
  }
  const intent = await stripe.paymentIntents.cancel(paymentIntentId);
  return { status: intent.status, mock: false };
}

export async function retrieveIntent(paymentIntentId: string) {
  const stripe = getStripe();
  if (!stripe || paymentIntentId.startsWith('pi_mock_')) {
    return { id: paymentIntentId, status: 'requires_capture' as const, mock: true };
  }
  const intent = await stripe.paymentIntents.retrieve(paymentIntentId);
  return { id: intent.id, status: intent.status, mock: false };
}

export function paymentsMode(): 'stripe' | 'mock' {
  return isStripeEnabled ? 'stripe' : 'mock';
}
