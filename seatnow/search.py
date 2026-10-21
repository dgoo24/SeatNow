"""Búsqueda de conexiones con disponibilidad en tiempo real."""

from __future__ import annotations

from datetime import date
from typing import Any

from seatnow import gtfs, inventory, store
from seatnow.format import train_label


def _in_window(minutes: int, time_from: int | None, time_to: int | None) -> bool:
    if time_from is not None and minutes < time_from:
        return False
    return not (time_to is not None and minutes > time_to)


def search_trains(
    feed: gtfs.Feed,
    *,
    origin: str | None = None,
    destination: str | None = None,
    day: date | None = None,
    time_type: str = "departure",
    time_from: int | None = None,
    time_to: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Devuelve las conexiones que cumplen origen/destino y ventana horaria.

    `origin`/`destination` vacíos significan "cualquiera": se usa la primera y
    la última parada del viaje respectivamente.
    """
    results: list[dict[str, Any]] = []
    bookings_by_trip: dict[str, list[dict[str, Any]]] = {}
    for booking in store.active_bookings():
        bookings_by_trip.setdefault(booking["trip_id"], []).append(booking)

    for trip in feed.trips.values():
        stops = trip.stops
        if len(stops) < 2:
            continue

        candidates = [s for s in stops if s.stop_name == origin] if origin else [stops[0]]
        targets = [s for s in stops if s.stop_name == destination] if destination else [stops[-1]]

        segment = None
        for start in candidates:
            for end in targets:
                if start.sequence >= end.sequence:
                    continue
                segment = feed.segment_between(trip.trip_id, start.stop_name, end.stop_name)
                if segment is not None:
                    break
            if segment is not None:
                break
        if segment is None:
            continue

        reference = gtfs.time_to_minutes(segment.departure if time_type == "departure" else segment.arrival)
        if not _in_window(reference, time_from, time_to):
            continue

        state = inventory.availability_for_segment(
            feed, segment.id, bookings_by_trip.get(trip.trip_id, [])
        )
        available = inventory.count_available(state)

        results.append(
            {
                "trip_id": trip.trip_id,
                "route_name": trip.route_name,
                "train": train_label(trip.route_name),
                "origin_stop": segment.origin_stop,
                "destination_stop": segment.destination_stop,
                "departure": segment.departure,
                "arrival": segment.arrival,
                "duration_minutes": segment.duration_minutes,
                "distance_km": segment.distance_km,
                "price": segment.price,
                "total_seats": len(state),
                "available_seats": available,
                "segment_id": segment.id,
            }
        )

    results.sort(key=lambda row: (row["departure"], row["route_name"]))
    return results[:limit]
