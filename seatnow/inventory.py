"""Inventario de asientos, holds, pago, cancelación, check-in y no-show.

Equivalente a `backend/src/modules/inventory/simulated.ts` +
`backend/src/modules/bookings/bookings.service.ts` + `checkin.service.ts`.
El inventario no se guarda: se deriva de las reservas (parámetros del
backend: la unidad de recurso es el par asiento + tramo).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from seatnow import gtfs, store
from seatnow.config import (
    NO_SHOW_GRACE_MINUTES,
    NO_SHOW_MAX_AGE_HOURS,
    SEAT_HOLD_TTL_SECONDS,
)

AVAILABLE = "AVAILABLE"
HELD = "HELD"
BOOKED = "BOOKED"


class InventoryError(Exception):
    """Error de negocio con mensaje listo para mostrar al usuario."""


def trip_snapshot(feed: gtfs.Feed, trip_id: str, segment_id: str) -> str:
    """Guarda con la reserva los datos del viaje/tramo (el feed es efímero)."""
    trip = feed.trips.get(trip_id)
    segment = feed.segments.get(segment_id)
    data = {
        "route_name": trip.route_name if trip else "",
        "train_number": trip.train_number if trip else "",
        "origin_stop": segment.origin_stop if segment else "",
        "destination_stop": segment.destination_stop if segment else "",
        "departure": segment.departure if segment else "",
        "arrival": segment.arrival if segment else "",
        "price": segment.price if segment else 0.0,
    }
    return json.dumps(data, ensure_ascii=False)


def booking_snapshot(booking: dict[str, Any]) -> dict[str, Any]:
    """Datos del viaje guardados con la reserva (vacío si no los hay)."""
    raw = booking.get("details")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def segments_overlap(a: gtfs.Segment, b: gtfs.Segment) -> bool:
    """Dos intervalos se solapan si comparten al menos una parada.

    Frontera compartida (`0..3` vs `3..5`) NO se solapa: el pasajero baja en
    la parada 3 y otro sube en la misma parada.
    """
    return a.origin_sequence < b.destination_sequence and b.origin_sequence < a.destination_sequence


def availability_for_segment(
    feed: gtfs.Feed, segment_id: str, bookings: list[dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Estado de cada asiento del viaje en el tramo indicado.

    Devuelve `{seat_id: {status, reservations: [...]}}`.
    """
    segment = feed.segments.get(segment_id)
    if segment is None:
        return {}

    if bookings is None:
        bookings = store.active_bookings_for_trip(segment.trip_id)

    state: dict[str, dict[str, Any]] = {}

    for seat_id in gtfs.seat_ids_for_trip(segment.trip_id):
        reservations: list[dict[str, Any]] = []
        status = AVAILABLE

        for booking in bookings:
            if booking["seat_id"] != seat_id:
                continue
            booked_segment = feed.segments.get(booking["segment_id"])
            if booked_segment is None or not segments_overlap(segment, booked_segment):
                continue

            expires_at = _parse_iso(booking.get("expires_at"))
            expired = (
                booking["status"] == "PENDING_PAYMENT"
                and expires_at is not None
                and expires_at <= _now()
            )
            reservation = {
                "booking_id": booking["id"],
                "segment_id": booked_segment.id,
                "from": booked_segment.origin_stop,
                "to": booked_segment.destination_stop,
                "status": booking["status"],
            }
            if expired:
                reservation["status"] = "EXPIRED"
            else:
                reservations.append(reservation)
                if booking["status"] == "CONFIRMED":
                    status = BOOKED
                elif status != BOOKED:
                    status = HELD

        state[seat_id] = {"status": status, "reservations": reservations}

    return state


def count_available(state: dict[str, dict[str, Any]]) -> int:
    return sum(1 for value in state.values() if value["status"] == AVAILABLE)


def hold_seat(
    feed: gtfs.Feed,
    *,
    user_id: str,
    trip_id: str,
    seat_id: str,
    segment_id: str,
    ttl_seconds: int = SEAT_HOLD_TTL_SECONDS,
) -> dict[str, Any]:
    """Bloquea un asiento en un tramo durante `ttl_seconds`."""
    segment = feed.segments.get(segment_id)
    if segment is None or segment.trip_id != trip_id:
        raise InventoryError("Der gewählte Abschnitt gehört nicht zu diesem Zug.")
    if seat_id not in gtfs.seat_ids_for_trip(trip_id):
        raise InventoryError("Der Sitzplatz gehört nicht zu diesem Zug.")

    with store.transaction():
        bookings = store.active_bookings_for_trip(trip_id)
        state = availability_for_segment(feed, segment_id, bookings)
        current = state.get(seat_id)
        if current is None:
            raise InventoryError("Unbekannter Sitzplatz.")
        if current["status"] == BOOKED:
            raise InventoryError("Dieser Sitzplatz ist bereits gebucht.")
        if current["status"] == HELD:
            raise InventoryError(
                "Dieser Sitzplatz wird gerade von einem anderen Fahrgast reserviert."
            )

        expires_at = (_now() + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        booking = store.create_booking(
            user_id=user_id,
            trip_id=trip_id,
            seat_id=seat_id,
            segment_id=segment_id,
            status="PENDING_PAYMENT",
            total_amount=segment.price,
            expires_at=expires_at,
            details=trip_snapshot(feed, trip_id, segment_id),
        )
    return booking


def checkout(feed: gtfs.Feed, *, booking_id: str, user_id: str | None = None) -> dict[str, Any]:
    """Confirma la reserva (pago mock) y genera el QR."""
    booking = store.get_booking(booking_id)
    if booking is None:
        raise InventoryError("Buchung nicht gefunden.")
    if user_id is not None and booking["user_id"] != user_id:
        raise InventoryError("Diese Buchung gehört nicht zu Ihnen.")
    if booking["status"] != "PENDING_PAYMENT":
        raise InventoryError("Diese Buchung ist nicht mehr in Zahlung.")

    expires_at = _parse_iso(booking.get("expires_at"))
    if expires_at is not None and expires_at <= _now():
        store.update_booking(
            booking_id,
            status="FAILED",
            failure_reason="Zahlungsfrist abgelaufen",
            expires_at=None,
        )
        raise InventoryError("Die Zahlungsfrist ist abgelaufen.")

    qr_payload = f"TKT-{booking['id'][:16].upper()}"
    updated = store.update_booking(
        booking_id,
        status="CONFIRMED",
        qr_payload=qr_payload,
        expires_at=None,
        failure_reason=None,
    )
    assert updated is not None
    return updated


def cancel_booking(booking_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    booking = store.get_booking(booking_id)
    if booking is None:
        raise InventoryError("Buchung nicht gefunden.")
    if user_id is not None and booking["user_id"] != user_id:
        raise InventoryError("Diese Buchung gehört nicht zu Ihnen.")
    updated = store.update_booking(
        booking_id,
        status="CANCELLED",
        expires_at=None,
        failure_reason="vom Benutzer storniert",
    )
    assert updated is not None
    return updated


def check_in(booking_id: str, *, qr_payload: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    booking = store.get_booking(booking_id)
    if booking is None:
        raise InventoryError("Buchung nicht gefunden.")
    if user_id is not None and booking["user_id"] != user_id:
        raise InventoryError("Diese Buchung gehört nicht zu Ihnen.")
    if booking["status"] == "NO_SHOW":
        raise InventoryError("Der Sitzplatz wurde wegen No-Show freigegeben.")
    if booking["status"] != "CONFIRMED":
        raise InventoryError("Nur bestätigte Buchungen können eingecheckt werden.")
    if qr_payload and qr_payload.strip().upper() != (booking["qr_payload"] or "").upper():
        raise InventoryError("QR-Code stimmt nicht mit dieser Buchung überein.")

    if booking["is_checked_in"] and booking["checked_in_at"]:
        return booking

    updated = store.update_booking(
        booking_id,
        is_checked_in=1,
        checked_in_at=_now().isoformat(timespec="seconds"),
    )
    assert updated is not None
    return updated


def sweep_expired_holds() -> int:
    """Libera los holds cuya fecha de pago ha vencido."""
    now = _now()
    released = 0
    for booking in store.list_bookings():
        if booking["status"] != "PENDING_PAYMENT":
            continue
        expires_at = _parse_iso(booking.get("expires_at"))
        if expires_at is None or expires_at > now:
            continue
        store.update_booking(
            booking["id"],
            status="FAILED",
            failure_reason="Reservierung abgelaufen",
            expires_at=None,
        )
        released += 1
    return released


def sweep_no_show(feed: gtfs.Feed, *, reference: datetime | None = None) -> list[str]:
    """Marca NO_SHOW las reservas confirmadas sin check-in fuera de plazo."""
    now = reference or _now()
    released: list[str] = []

    for booking in store.list_bookings():
        if booking["status"] != "CONFIRMED" or booking["is_checked_in"]:
            continue
        segment = feed.segments.get(booking["segment_id"])
        if segment is None:
            continue
        trip = feed.trips.get(segment.trip_id)
        if trip is None:
            continue

        departure = _departure_datetime(segment, now)
        if departure is None:
            continue
        elapsed = (now - departure).total_seconds() / 60
        if elapsed < NO_SHOW_GRACE_MINUTES or elapsed > NO_SHOW_MAX_AGE_HOURS * 60:
            continue

        store.update_booking(
            booking["id"],
            status="NO_SHOW",
            failure_reason=f"Kein Check-in nach Abfahrt (No-Show-Regel: {NO_SHOW_GRACE_MINUTES} Min.)",
            expires_at=None,
        )
        released.append(booking["id"])

    return released


def _departure_datetime(segment: gtfs.Segment, now: datetime) -> datetime | None:
    """Instante de salida del tramo en el día de servicio (día local)."""
    from seatnow.format import local_today

    day = local_today(now)
    minutes = gtfs.time_to_minutes(segment.departure)
    return day.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)


def new_booking_id() -> str:
    return uuid.uuid4().hex
