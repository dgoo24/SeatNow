import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { prisma, disconnectDatabase } from '../../shared/database/prisma';
import { disconnectRedis } from '../../shared/redis/redis';
import { ConflictError } from '../../shared/errors';
import { intervalsOverlap, SimulatedProvider } from './simulated';

/**
 * Prueba de la reutilizacion de asiento por tramo (Teilstrecken):
 * un asiento reservado en un tramo debe quedar LIBRE en otro tramo que no se solape
 * y OCUPADO (y no reservable) en los tramos que si se solapan.
 *
 * Necesita Postgres (5433) y Redis (6379) de Docker arriba.
 */

describe('intervalsOverlap (regla de solape por secuencia de paradas)', () => {
  test('tramos que comparten paradas se solapan -> true', () => {
    assert.equal(intervalsOverlap({ from: 0, to: 3 }, { from: 2, to: 5 }), true);
  });

  test('frontera compartida (baja uno, sube otro) no se solapa -> false', () => {
    assert.equal(intervalsOverlap({ from: 0, to: 3 }, { from: 3, to: 5 }), false);
  });

  test('tramos disjuntos -> false', () => {
    assert.equal(intervalsOverlap({ from: 0, to: 2 }, { from: 3, to: 5 }), false);
  });

  test('orden invertido sigue detectando el solape -> true', () => {
    assert.equal(intervalsOverlap({ from: 2, to: 5 }, { from: 0, to: 3 }), true);
  });

  test('orden invertido sin solape -> false', () => {
    assert.equal(intervalsOverlap({ from: 5, to: 8 }, { from: 0, to: 3 }), false);
  });
});

describe('SimulatedProvider: reutilizacion de asiento por tramo (fixture aislado)', () => {
  const provider = new SimulatedProvider();
  const tag = randomUUID().slice(0, 8);

  let userId: string;
  let tripId: string;
  let seatId: string;
  let segA: string; // [0,2]
  let segB: string; // [2,4] disjunto de A
  let segC: string; // [1,3] solapa con A y B
  const heldBookingIds: string[] = [];

  before(async () => {
    const user = await prisma.user.create({
      data: {
        email: `overlap-test-${tag}@trenes.local`,
        passwordHash: 'not-a-real-hash',
        fullName: 'Overlap Test',
      },
    });
    userId = user.id;

    const trip = await prisma.trip.create({
      data: {
        tripId: `TEST-OVERLAP-${tag}`,
        routeName: 'TEST Overlap',
        originStop: 'P0',
        destinationStop: 'P4',
        departureTime: '10:00:00',
        arrivalTime: '12:00:00',
        price: 10,
      },
    });
    tripId = trip.id;

    const seat = await prisma.seat.create({
      data: { tripId, seatNumber: '1', carNumber: 1 },
    });
    seatId = seat.id;

    const makeSegment = (originSequence: number, destinationSequence: number) =>
      prisma.segment.create({
        data: {
          tripId,
          originStopName: `P${originSequence}`,
          destinationStopName: `P${destinationSequence}`,
          originSequence,
          destinationSequence,
          departureTime: '10:00:00',
          arrivalTime: '12:00:00',
          price: 10,
        },
      });

    segA = (await makeSegment(0, 2)).id;
    segB = (await makeSegment(2, 4)).id;
    segC = (await makeSegment(1, 3)).id;
  });

  after(async () => {
    // Liberar locks de Redis de los holds creados.
    for (const id of heldBookingIds) {
      await provider.cancelBooking(id).catch(() => {});
    }
    await prisma.booking.deleteMany({ where: { tripId } }).catch(() => {});
    if (tripId) await prisma.trip.delete({ where: { id: tripId } }).catch(() => {});
    if (userId) await prisma.user.delete({ where: { id: userId } }).catch(() => {});
    await disconnectDatabase();
    await disconnectRedis();
  });

  test('asiento retenido en un tramo sigue AVAILABLE en un tramo disjunto', async () => {
    const held = await provider.holdSeat({ userId, tripId, seatId, segmentId: segA, ttlSeconds: 60 });
    heldBookingIds.push(held.booking.id);

    const [availA, availB, availC] = await Promise.all([
      provider.getSegmentAvailability(segA),
      provider.getSegmentAvailability(segB),
      provider.getSegmentAvailability(segC),
    ]);

    const statusIn = (list: { seatId: string; status: string }[]) =>
      list.find((s) => s.seatId === seatId)?.status;

    assert.equal(statusIn(availA), 'HELD', 'el tramo reservado debe estar HELD');
    assert.equal(statusIn(availC), 'HELD', 'un tramo solapado debe estar HELD');
    assert.equal(statusIn(availB), 'AVAILABLE', 'un tramo disjunto debe quedar LIBRE');
  });

  test('retener el mismo asiento en un tramo solapado -> ConflictError', async () => {
    await assert.rejects(
      provider.holdSeat({ userId, tripId, seatId, segmentId: segC, ttlSeconds: 60 }),
      (err) => err instanceof ConflictError,
    );
  });

  test('retener el mismo asiento en el tramo disjunto -> succeed', async () => {
    const held = await provider.holdSeat({ userId, tripId, seatId, segmentId: segB, ttlSeconds: 60 });
    heldBookingIds.push(held.booking.id);
    assert.equal(held.booking.segmentId, segB);
    assert.equal(held.booking.seatId, seatId);
  });
});
