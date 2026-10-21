import { z } from 'zod';

export const holdSchema = z.object({
  tripId: z.string().uuid('tripId ungültig'),
  seatId: z.string().uuid('seatId ungültig'),
  segmentId: z.string().uuid('segmentId ungültig'),
});

export const checkoutSchema = z.object({
  bookingId: z.string().uuid('bookingId ungültig'),
  paymentMethodId: z.string().min(1).optional(),
});

export const cancelSchema = z.object({
  bookingId: z.string().uuid('bookingId ungültig'),
});

export const checkInSchema = z.object({
  bookingId: z.string().uuid('bookingId ungültig'),
  /** Payload QR del boarding pass (escaneo simulado). Opcional. */
  qrPayload: z.string().min(1).optional(),
});

export type HoldInput = z.infer<typeof holdSchema>;
export type CheckoutInput = z.infer<typeof checkoutSchema>;
export type CancelInput = z.infer<typeof cancelSchema>;
export type CheckInInput = z.infer<typeof checkInSchema>;