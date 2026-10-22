"""Estado en vivo del tren (simulado).

El backend consulta la API pública de Deutsche Bahn (`bahn.de`, HAFAS). Ese
proveedor no cubre la red española del feed GTFS de este repositorio, así que
la app de Streamlit simula el estado en vivo de forma determinista por
`(trip_id, fecha)`: el mismo tren muestra siempre el mismo retraso durante el
mismo día, y cambia al día siguiente.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from seatnow import gtfs

DELAYS = (0, 0, 0, 0, 0, 3, 5, 8, 12, 17, 25, 35, 47)


@dataclass
class LiveStatus:
    available: bool
    delay_minutes: int
    cancelled: bool
    platform: str
    planned_platform: str
    message: str

    @property
    def headline(self) -> str:
        if self.cancelled:
            return "AUSGEFALLEN"
        if self.delay_minutes > 0:
            return f"Verspätung {self.delay_minutes} Min."
        return "pünktlich"

    @property
    def severity(self) -> str:
        if self.cancelled:
            return "error"
        if self.delay_minutes > 0:
            return "warning"
        return "success"


def _seed(trip_id: str, day: date) -> int:
    digest = hashlib.sha256(f"{trip_id}|{day.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _live_from_data(live: dict) -> LiveStatus:
    """Estado en vivo real devuelto por la DB API (`/fchg`)."""
    delay = int(live.get("delay_minutes") or 0)
    cancelled = bool(live.get("cancelled"))
    platform = str(live.get("platform") or "")
    planned_platform = str(live.get("planned_platform") or "")

    if cancelled:
        message = "Der Zug fällt heute aus. Wir suchen automatisch eine alternative Verbindung."
    elif delay >= 25:
        message = "Erhebliche Verspätung am Startbahnhof. Wir informieren dich automatisch."
    elif delay > 0:
        message = "Leichte Verspätung im Betriebsablauf."
    else:
        message = "Der Zug fährt pünktlich."

    return LiveStatus(
        available=True,
        delay_minutes=delay,
        cancelled=cancelled,
        platform=platform,
        planned_platform=planned_platform,
        message=message,
    )


def live_status(trip: gtfs.Trip, day: date) -> LiveStatus:
    if trip.live:
        return _live_from_data(trip.live)

    seed = _seed(trip.trip_id, day)
    delay = DELAYS[seed % len(DELAYS)]
    cancelled = (seed >> 8) % 100 < 6
    platform = str(1 + (seed >> 16) % 14)
    planned_platform = str(1 + (seed >> 24) % 12)

    if cancelled:
        message = "Der Zug fällt heute aus. Wir suchen automatisch eine alternative Verbindung."
    elif delay >= 25:
        message = "Erhebliche Verspätung am Startbahnhof. Wir informieren dich automatisch."
    elif delay > 0:
        message = "Leichte Verspätung im Betriebsablauf."
    else:
        message = "Der Zug fährt pünktlich."

    return LiveStatus(
        available=True,
        delay_minutes=delay,
        cancelled=cancelled,
        platform=platform,
        planned_platform=planned_platform,
        message=message,
    )


def stop_delays(trip: gtfs.Trip, day: date, status: LiveStatus) -> list[dict[str, object]]:
    """Retraso estimado en cada parada, Growing a lo largo del trayecto."""
    if trip.live:
        return _stop_delays_live(trip, status)

    seed = _seed(trip.trip_id, day)
    rows: list[dict[str, object]] = []
    for index, stop in enumerate(trip.stops):
        drift = status.delay_minutes
        if status.cancelled:
            drift = 0
        elif index:
            drift = min(status.delay_minutes + (seed >> (index % 12)) % 4, 120)
        rows.append(
            {
                "name": stop.stop_name,
                "arrival": stop.arrival,
                "departure": stop.departure,
                "delay_minutes": drift,
                "cancelled": status.cancelled,
            }
        )
    return rows


def _stop_delays_live(trip: gtfs.Trip, status: LiveStatus) -> list[dict[str, object]]:
    """Retrasos reales por parada (datos de la DB API)."""
    stops_live = (trip.live or {}).get("stops") or {}
    rows: list[dict[str, object]] = []
    for stop in trip.stops:
        info = stops_live.get(stop.stop_name) or {}
        rows.append(
            {
                "name": stop.stop_name,
                "arrival": stop.arrival,
                "departure": stop.departure,
                "delay_minutes": int(info.get("delay_minutes") or 0),
                "cancelled": bool(info.get("cancelled") or status.cancelled),
                "platform": info.get("platform"),
            }
        )
    return rows
