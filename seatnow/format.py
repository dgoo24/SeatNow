"""Formato y utilidades de presentación (es-ES)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seatnow.config import TIMEZONE

try:
    _TZ = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:  # pragma: no cover - sistemas sin tzdata
    _TZ = timezone.utc


def local_now() -> datetime:
    return datetime.now(_TZ)


def local_today(reference: datetime | None = None) -> datetime:
    """Inicio del día local (Europe/Madrid) con zona horaria."""
    now = reference or local_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_TZ)
    local = now.astimezone(_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def fmt_money(amount: float) -> str:
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_duration(minutes: int | None) -> str:
    if minutes is None:
        return "–"
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def fmt_clock(value: str | None) -> str:
    """'HH:MM:SS' -> 'HH:MM'."""
    if not value:
        return "–"
    parts = value.split(":")
    if len(parts) < 2:
        return value
    return f"{parts[0]}:{parts[1]}"


def train_label(route_name: str) -> str:
    """'AVE Madrid - Barcelona' -> 'AVE' (prefijo de producto)."""
    match = re.match(r"^([A-Za-zÀ-ÿ]{2,6})\s", route_name.strip())
    if match:
        return match.group(1).upper()
    return route_name.split(" - ")[0].strip().upper()


def status_label(status: str) -> str:
    return {
        "PENDING_PAYMENT": "Reserviert (Zahlung offen)",
        "CONFIRMED": "Bestätigt",
        "FAILED": "Abgelaufen",
        "CANCELLED": "Storniert",
        "NO_SHOW": "No-Show",
        "CANCEL_PENDING_REFUND": "Erstattung ausstehend",
    }.get(status, status)


def seat_label(seat_id: str) -> str:
    """`<trip>:<car>:<number>` -> 'Wagen 1 · 07'."""
    parts = seat_id.rsplit(":", 2)
    if len(parts) != 3:
        return seat_id
    return f"Wagen {parts[1]} · {parts[2]}"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
