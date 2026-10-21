import type { ServerResponse } from 'node:http';

export type SseEventType =
  | 'hello'
  | 'seat:update'
  | 'booking:update'
  | 'realtime'
  /** El hold en Redis expiro (TTL) y el asiento volvio a estar libre. */
  | 'hold:expired'
  | 'server:time'
  | 'ping';

export interface SseClient {
  id: string;
  tripId: string;
  userId?: string;
  res: ServerResponse;
  createdAt: number;
}

/**
 * Broker de Server-Sent Events en memoria.
 * Mantiene subscripciones por tripId para notificar en tiempo real
 * los cambios del mapa de asientos y del estado de la reserva.
 */
class SseBroker {
  private clients = new Map<string, SseClient>();

  subscribe(client: SseClient): void {
    this.clients.set(client.id, client);
  }

  unsubscribe(clientId: string): void {
    this.clients.delete(clientId);
  }

  getClientsForTrip(tripId: string): SseClient[] {
    return [...this.clients.values()].filter((c) => c.tripId === tripId);
  }

  /** Viajes con al menos un cliente SSE conectado (para el poller en vivo). */
  activeTripIds(): string[] {
    return [...new Set([...this.clients.values()].map((c) => c.tripId))];
  }

  count(): number {
    return this.clients.size;
  }

  private write(client: SseClient, event: SseEventType, data: unknown): void {
    try {
      client.res.write(`event: ${event}\n`);
      client.res.write(`data: ${JSON.stringify(data)}\n\n`);
    } catch {
      this.unsubscribe(client.id);
    }
  }

  sendToClient(clientId: string, event: SseEventType, data: unknown): void {
    const client = this.clients.get(clientId);
    if (client) this.write(client, event, data);
  }

  broadcastToTrip(tripId: string, event: SseEventType, data: unknown): void {
    for (const client of this.getClientsForTrip(tripId)) {
      this.write(client, event, data);
    }
  }

  /** Notifica a un usuario concreto conectado a un viaje. */
  sendToUser(tripId: string, userId: string, event: SseEventType, data: unknown): void {
    for (const client of this.getClientsForTrip(tripId)) {
      if (client.userId === userId) this.write(client, event, data);
    }
  }

  pingAll(): void {
    for (const client of this.clients.values()) {
      try {
        client.res.write(': ping\n\n');
      } catch {
        this.unsubscribe(client.id);
      }
    }
  }
}

export const sseBroker = new SseBroker();
