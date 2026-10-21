"""Carga del feed GTFS y construcción del modelo de viajes/tramos/asientos.

Equivalente a `backend/src/modules/trains/ingest-gtfs.ts` + `gtfs-parser.ts`.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from seatnow.config import CAR_COUNT, SEATS_PER_CAR


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class TripStop:
    stop_id: str
    stop_name: str
    arrival: str
    departure: str
    sequence: int


@dataclass(frozen=True)
class Segment:
    """Un tramo (Teilstrecke) de un viaje: de una parada a otra."""

    id: str
    trip_id: str
    origin_sequence: int
    destination_sequence: int
    origin_stop: str
    destination_stop: str
    departure: str
    arrival: str
    duration_minutes: int
    distance_km: float
    price: float


@dataclass
class Trip:
    trip_id: str
    route_id: str
    route_name: str
    headsign: str
    origin_stop: str
    destination_stop: str
    departure: str
    arrival: str
    duration_minutes: int
    price: float
    stops: list[TripStop] = field(default_factory=list)


@dataclass
class Feed:
    stops: dict[str, Stop] = field(default_factory=dict)
    trips: dict[str, Trip] = field(default_factory=dict)
    segments: dict[str, Segment] = field(default_factory=dict)
    segments_by_trip: dict[str, list[Segment]] = field(default_factory=dict)

    @property
    def stop_names(self) -> list[str]:
        """Paradas usadas realmente por algún viaje (pool de búsqueda)."""
        used = {s.origin_stop for s in self.segments.values()}
        used |= {s.destination_stop for s in self.segments.values()}
        return sorted(used)

    def segment_between(self, trip_id: str, origin: str, destination: str) -> Segment | None:
        """Tramo exacto `origin -> destination` dentro de un viaje."""
        for seg in self.segments_by_trip.get(trip_id, []):
            if seg.origin_stop == origin and seg.destination_stop == destination:
                return seg
        return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def _opt_float(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def time_to_minutes(value: str) -> int:
    """'HH:MM:SS' -> minutos desde medianoche (con wrap de 24h)."""
    parts = value.split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    return hours * 60 + minutes


def duration_minutes(departure: str, arrival: str) -> int:
    diff = time_to_minutes(arrival) - time_to_minutes(departure)
    if diff <= 0:
        diff += 1440
    return diff


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_trip_price(duration: int, stop_count: int) -> float:
    """Misma fórmula que `computeTripPrice` del backend."""
    return round(19.9 + (stop_count - 1) * 4.5 + (duration / 60) * 6, 2)


def compute_segment_price(distance_km: float) -> float:
    """Misma fórmula que `computeSegmentPrice` del backend."""
    return round(min(max(3.4 + 0.005 * distance_km, 4.90), 5.90), 2)


def _distance_between(stops: dict[str, Stop], a: TripStop, b: TripStop) -> float:
    stop_a = stops.get(a.stop_id)
    stop_b = stops.get(b.stop_id)
    if not stop_a or not stop_b or stop_a.lat is None or stop_b.lat is None:
        return 0.0
    if stop_a.lon is None or stop_b.lon is None:
        return 0.0
    return round(haversine_km(stop_a.lat, stop_a.lon, stop_b.lat, stop_b.lon), 2)


def load_feed(directory: Path) -> Feed:
    """Carga routes/stops/trips/stop_times y deriva viajes, tramos y asientos."""
    directory = Path(directory)
    feed = Feed()

    for row in _read_csv(directory / "stops.txt"):
        stop_id = row.get("stop_id", "")
        if not stop_id:
            continue
        feed.stops[stop_id] = Stop(
            stop_id=stop_id,
            name=row.get("stop_name", stop_id),
            lat=_opt_float(row.get("stop_lat")),
            lon=_opt_float(row.get("stop_lon")),
        )

    routes = {r.get("route_id", ""): r for r in _read_csv(directory / "routes.txt") if r.get("route_id")}

    stop_times: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv(directory / "stop_times.txt"):
        stop_times.setdefault(row.get("trip_id", ""), []).append(row)

    for row in _read_csv(directory / "trips.txt"):
        trip_id = row.get("trip_id", "")
        if not trip_id:
            continue
        rows = sorted(stop_times.get(trip_id, []), key=lambda r: int(r.get("stop_sequence", 0) or 0))
        if len(rows) < 2:
            continue

        route = routes.get(row.get("route_id", ""), {})
        route_name = route.get("route_long_name") or route.get("route_short_name") or trip_id

        trip_stops: list[TripStop] = []
        for r in rows:
            stop_id = r.get("stop_id", "")
            stop = feed.stops.get(stop_id)
            trip_stops.append(
                TripStop(
                    stop_id=stop_id,
                    stop_name=stop.name if stop else stop_id,
                    arrival=r.get("arrival_time", r.get("departure_time", "00:00:00")),
                    departure=r.get("departure_time", r.get("arrival_time", "00:00:00")),
                    sequence=int(r.get("stop_sequence", 0) or 0),
                )
            )

        departure = trip_stops[0].departure
        arrival = trip_stops[-1].arrival
        duration = duration_minutes(departure, arrival)

        feed.trips[trip_id] = Trip(
            trip_id=trip_id,
            route_id=row.get("route_id", ""),
            route_name=route_name,
            headsign=row.get("trip_headsign", ""),
            origin_stop=trip_stops[0].stop_name,
            destination_stop=trip_stops[-1].stop_name,
            departure=departure,
            arrival=arrival,
            duration_minutes=duration,
            price=compute_trip_price(duration, len(trip_stops)),
            stops=trip_stops,
        )

        trip_segments: list[Segment] = []
        for i in range(len(trip_stops)):
            for j in range(i + 1, len(trip_stops)):
                origin, destination = trip_stops[i], trip_stops[j]
                distance = _distance_between(feed.stops, origin, destination)
                segment = Segment(
                    id=f"{trip_id}:{origin.sequence}:{destination.sequence}",
                    trip_id=trip_id,
                    origin_sequence=origin.sequence,
                    destination_sequence=destination.sequence,
                    origin_stop=origin.stop_name,
                    destination_stop=destination.stop_name,
                    departure=origin.departure,
                    arrival=destination.arrival,
                    duration_minutes=duration_minutes(origin.departure, destination.arrival),
                    distance_km=distance,
                    price=compute_segment_price(distance),
                )
                feed.segments[segment.id] = segment
                trip_segments.append(segment)

        feed.segments_by_trip[trip_id] = trip_segments

    return feed


def seat_ids_for_trip(trip_id: str) -> list[str]:
    """Ids de asiento deterministas: `<trip>:<coche>:<numero>`."""
    return [
        f"{trip_id}:{car}:{number:02d}"
        for car in range(1, CAR_COUNT + 1)
        for number in range(1, SEATS_PER_CAR + 1)
    ]


def parse_seat_id(seat_id: str) -> tuple[int, int]:
    """`<trip>:<coche>:<numero>` -> (coche, numero)."""
    parts = seat_id.rsplit(":", 2)
    if len(parts) != 3:
        return 0, 0
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return 0, 0
