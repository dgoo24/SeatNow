"""Feed de viajes con datos reales de la Deutsche Bahn.

Convierte las respuestas de la Timetables API (horario planificado + cambios
en vivo) en el modelo `gtfs.Feed` que usan el resto de páginas (búsqueda,
mapa de asientos, reservas, administración).

El inventario de asientos sigue siendo simulado (la DB no publica plazas por
tramo), pero horarios, trenes, vías, retrasos y cancelaciones son reales.

La API del marketplace está limitada a 60 peticiones por minuto, así que cada
búsqueda trabaja con un presupuesto (`CallBudget`) y las respuestas se cachean
en Streamlit.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import streamlit as st

from seatnow import dbapi, gtfs, inventory
from seatnow.config import DB_API_MAX_STOPS, DB_API_MAX_TRAINS, DB_API_WORKERS
from seatnow.dbapi import DbApiError, Station, StopEvent
from seatnow.format import train_label

# Estaciones principales ofrecidas como atajo en el buscador (el EVA real se
# resuelve contra la API, así que no hace falta mantenerlo a mano).
STATION_SUGGESTIONS = (
    "Berlin Hbf",
    "Berlin Südkreuz",
    "Berlin Ostbahnhof",
    "Berlin Ostkreuz",
    "Berlin Spandau",
    "Berlin Zoologischer Garten",
    "Berlin Gesundbrunnen",
    "Berlin Potsdamer Platz",
    "Hamburg Hbf",
    "Hamburg-Altona",
    "Hamburg-Harburg",
    "Hamburg Dammtor",
    "München Hbf",
    "München Ostbahnhof",
    "München-Pasing",
    "Frankfurt (Main) Hbf",
    "Frankfurt (Main) Flughafen Fernbf",
    "Frankfurt (Main) Süd",
    "Köln Hbf",
    "Köln Messe/Deutz",
    "Stuttgart Hbf",
    "Düsseldorf Hbf",
    "Dortmund Hbf",
    "Essen Hbf",
    "Hannover Hbf",
    "Leipzig Hbf",
    "Dresden Hbf",
    "Nürnberg Hbf",
    "Augsburg Hbf",
    "Mannheim Hbf",
    "Karlsruhe Hbf",
    "Freiburg (Breisgau) Hbf",
    "Ulm Hbf",
    "Würzburg Hbf",
    "Erfurt Hbf",
    "Halle (Saale) Hbf",
    "Magdeburg Hbf",
    "Rostock Hbf",
    "Kiel Hbf",
    "Lübeck Hbf",
    "Mainz Hbf",
    "Saarbrücken Hbf",
    "Kassel-Wilhelmshöhe",
    "Braunschweig Hbf",
    "Bremen Hbf",
    "Osnabrück Hbf",
    "Münster (Westfalen) Hbf",
    "Bonn Hbf",
    "Koblenz Hbf",
    "Regensburg Hbf",
    "Passau Hbf",
    "Ingolstadt Hbf",
    "Wuppertal Hbf",
    "Oberhausen Hbf",
    "Göttingen Hbf",
    "Flensburg Hbf",
)

# Velocidad media por tipo de tren (km/h) para estimar distancias y precios.
SPEED_BY_CATEGORY = {
    "ICE": 120,
    "TGV": 120,
    "IC": 105,
    "EC": 105,
    "NJ": 110,
    "EN": 110,
    "RJX": 100,
    "RJ": 100,
    "IRE": 95,
    "IR": 90,
    "RE": 85,
    "RB": 75,
    "S": 50,
    "SN": 50,
    "E": 70,
    "R": 70,
    "M": 60,
}
DEFAULT_SPEED = 85

# Presupuesto de peticiones por búsqueda (límite del marketplace: 60/min).
SEARCH_BUDGET = 40
DESTINATION_LOOKUP_HOURS = (0, -1, 1, 2)
# Minutos estimados por tramo según la categoría del tren (para saber en qué
# hora hay que consultar el destino).
MINUTES_PER_HOP = {
    "ICE": 55,
    "IC": 45,
    "EC": 50,
    "TGV": 60,
    "AVE": 60,
    "NJ": 60,
    "RE": 25,
    "RB": 25,
    "S": 15,
    "STR": 15,
    "U": 10,
}
DEFAULT_MINUTES_PER_HOP = 35


# --------------------------------------------------------------------------- #
# Caché de red
# --------------------------------------------------------------------------- #

@st.cache_data(ttl="10m", show_spinner=False)
def cached_plan(eva: str, day: str, hour: int, client_id: str, api_key: str) -> list[StopEvent]:
    return dbapi.plan(eva, date.fromisoformat(day), hour, client_id=client_id, api_key=api_key)


@st.cache_data(ttl="2m", show_spinner=False)
def cached_changes(eva: str, client_id: str, api_key: str) -> dict[str, StopEvent]:
    return dbapi.changes(eva, client_id=client_id, api_key=api_key)


@st.cache_data(ttl="24h", show_spinner=False)
def cached_stations(pattern: str, client_id: str, api_key: str) -> list[tuple[str, str]]:
    return [
        (station.eva, station.name)
        for station in dbapi.find_stations(pattern, client_id=client_id, api_key=api_key)
    ]


# --------------------------------------------------------------------------- #
# Presupuesto de peticiones
# --------------------------------------------------------------------------- #

class CallBudget:
    """Contador de peticiones concurrentes contra la API de la DB."""

    def __init__(self, total: int = SEARCH_BUDGET) -> None:
        self._total = total
        self._used = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._total - self._used

    def take(self, amount: int = 1) -> bool:
        with self._lock:
            if self._used + amount > self._total:
                return False
            self._used += amount
            return True

    def split(self, parts: int) -> int:
        """Reparte el presupuesto restante entre `parts` trozos."""
        return max(1, self.remaining // max(parts, 1))


# --------------------------------------------------------------------------- #
# Estaciones
# --------------------------------------------------------------------------- #

def _name_variants(name: str) -> list[str]:
    """Variantes de búsqueda: tal cual, sin umlaut y con comodín."""
    text = name.strip()
    if not text:
        return []
    ascii_name = (
        text.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    )
    variants = [text]
    if ascii_name != text:
        variants.append(ascii_name)
    variants.extend([f"{variant}*" for variant in list(variants)])
    return list(dict.fromkeys(variants))


def find_stations(pattern: str) -> list[Station]:
    """Busca estaciones en la DB (vacío si no hay credenciales o error)."""
    creds = dbapi.credentials()
    if not creds or not pattern.strip():
        return []
    client_id, api_key = creds
    for variant in _name_variants(pattern):
        try:
            rows = cached_stations(variant, client_id, api_key)
        except DbApiError:
            continue
        if rows:
            return [Station(eva=eva, name=name) for eva, name in rows]
    return []


def resolve_station(name: str) -> Station | None:
    """Resuelve un nombre de estación a su número EVA."""
    stations = find_stations(name)
    if not stations:
        return None
    wanted = name.strip().casefold()
    for station in stations:
        if station.name.casefold() == wanted:
            return station
    for station in stations:
        if wanted and wanted in station.name.casefold():
            return station
    return stations[0]


def _resolve_station_safe(name: str) -> Station | None:
    try:
        return resolve_station(name)
    except DbApiError:
        return None


# --------------------------------------------------------------------------- #
# Tablero de salidas
# --------------------------------------------------------------------------- #

def search_hours(
    day: date,
    time_from: int | None,
    time_to: int | None,
    *,
    time_type: str = "departure",
    now: datetime | None = None,
    max_hours: int = 4,
) -> range:
    """Horas del día a consultar para la ventana de búsqueda."""
    reference = now or datetime.now(dbapi.LOCAL_TZ)
    is_today = day == reference.date()

    if time_from is not None:
        start_hour = min(23, max(0, time_from // 60))
    elif time_type == "arrival" and time_to is not None:
        start_hour = min(23, max(0, time_to // 60 - 2))
    else:
        start_hour = reference.hour if is_today else 0

    end_hour = (
        min(23, max(start_hour, time_to // 60))
        if time_to is not None
        else min(23, start_hour + max_hours - 1)
    )

    span = min(end_hour - start_hour + 1, max_hours)
    return range(start_hour, start_hour + span)


def departures(
    origin: Station,
    day: date,
    time_from: int | None,
    time_to: int | None,
    *,
    time_type: str = "departure",
    budget: CallBudget | None = None,
) -> list[StopEvent]:
    """Salidas reales de una estación en la ventana de búsqueda."""
    creds = dbapi.credentials()
    if not creds:
        return []
    client_id, api_key = creds
    hours = search_hours(day, time_from, time_to, time_type=time_type)
    events: list[StopEvent] = []
    for hour in hours:
        if budget is not None and not budget.take():
            break
        try:
            events.extend(cached_plan(origin.eva, day.isoformat(), hour, client_id, api_key))
        except DbApiError:
            continue

    if events:
        try:
            live = cached_changes(origin.eva, client_id, api_key)
        except DbApiError:
            live = {}
        events = [dbapi.merge_changes(event, live.get(event.id)) for event in events]

    events = [event for event in events if event.departure is not None and event.path]

    start = time_from if time_from is not None else 0
    end = time_to if time_to is not None else 24 * 60
    window: list[StopEvent] = []
    for event in events:
        departure = event.departure
        assert departure is not None
        minutes = departure.hour * 60 + departure.minute
        if time_type == "arrival":
            if minutes <= end:
                window.append(event)
        elif start <= minutes <= end:
            window.append(event)
    window.sort(key=lambda event: event.departure or datetime.min)
    return window


# --------------------------------------------------------------------------- #
# Horas de las paradas
# --------------------------------------------------------------------------- #

def _day_for_hour(day: date, hour: int) -> date:
    """Día natural al que pertenece una hora (puede ser el anterior o el siguiente)."""
    if hour < 0:
        return day - timedelta(days=1)
    if hour > 23:
        return day + timedelta(days=1)
    return day


def _lookup_at(
    station: Station,
    day: date,
    hour: int,
    category: str,
    number: str,
    origin: str,
    *,
    expected: datetime | None = None,
    budget: CallBudget | None = None,
    offsets: tuple[int, ...] = DESTINATION_LOOKUP_HOURS,
    max_gap_seconds: float = 900,
) -> StopEvent | None:
    """Busca el mismo tren en otra estación para conocer su hora allí."""
    creds = dbapi.credentials()
    if not creds:
        return None
    client_id, api_key = creds
    # Si el momento esperado cae en otro día (trenes nocturnos), la consulta se
    # hace en ese día para no perder el tren al cruzar medianoche.
    base_day = day
    if expected is not None:
        delta = (expected.date() - day).days
        if delta in (-1, 0, 1):
            base_day = expected.date()
    best: StopEvent | None = None
    best_gap: float | None = None
    for offset in offsets:
        candidate = hour + offset
        if candidate < -1 or candidate > 24:
            continue
        if budget is not None and not budget.take():
            break
        query_day = base_day + timedelta(days=1 if candidate >= 24 else (-1 if candidate < 0 else 0))
        try:
            events = cached_plan(
                station.eva,
                query_day.isoformat(),
                candidate % 24,
                client_id,
                api_key,
            )
        except DbApiError:
            continue
        for event in events:
            if not dbapi.matches(event, category, number, origin):
                continue
            moment = event.arrival or event.departure
            if expected is None or moment is None:
                return event
            gap = abs((moment - expected).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = event, gap
        # Si la coincidencia ya es precisa no hace falta seguir probando horas.
        if best_gap is not None and best_gap <= max_gap_seconds:
            break
    return best


def _stop_details(
    path: tuple[str, ...],
    day: date,
    start: datetime,
    end: datetime,
    category: str,
    number: str,
    origin: str,
    *,
    budget: CallBudget | None = None,
) -> dict[str, dict[str, Any]]:
    """Hora real (y estimada) de cada parada del camino del tren.

    Las paradas cuyo EVA no se resuelve o cuya consulta falla conservan la
    estimación lineal entre el origen y el destino.
    """
    if not path:
        return {}

    total = len(path)
    span = max((end - start).total_seconds() / 60, 1.0)
    details: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(path):
        ratio = (index + 1) / total
        moment = start + timedelta(minutes=span * ratio)
        details[name] = {
            "arrival": moment,
            "departure": moment,
            "delay_minutes": 0,
            "platform": None,
            "cancelled": False,
            "real": False,
        }

    if total <= 1:
        return details

    names = list(path)
    with ThreadPoolExecutor(max_workers=DB_API_WORKERS) as pool:
        resolved = list(pool.map(_resolve_station_safe, names))
    stations = dict(zip(names, resolved, strict=False))

    def _lookup(name: str) -> tuple[str, dict[str, Any]] | None:
        station = stations.get(name)
        if station is None:
            return None
        index = names.index(name)
        ratio = (index + 1) / total
        moment = start + timedelta(minutes=span * ratio)
        event = _lookup_at(
            station,
            day,
            moment.hour,
            category,
            number,
            origin,
            expected=moment,
            budget=budget,
            offsets=(0, -1, 1),
        )
        if event is None:
            return None
        arrival = event.arrival or event.departure
        departure = event.departure or event.arrival
        if arrival is None and departure is None:
            return None
        return name, {
            "arrival": arrival or details[name]["arrival"],
            "departure": departure or details[name]["departure"],
            "delay_minutes": event.delay_minutes,
            "platform": event.platform,
            "cancelled": event.cancelled,
            "real": True,
        }

    targets = [name for name in names if name != names[0]]
    if targets:
        try:
            with ThreadPoolExecutor(max_workers=DB_API_WORKERS) as pool:
                found = list(pool.map(_lookup, targets))
        except DbApiError:
            found = []
        for item in found:
            if item:
                details[item[0]] = item[1]

    details[names[0]] = {
        "arrival": start,
        "departure": start,
        "delay_minutes": 0,
        "platform": None,
        "cancelled": False,
        "real": True,
    }
    return details


# --------------------------------------------------------------------------- #
# Construcción del feed
# --------------------------------------------------------------------------- #

def _speed(category: str) -> int:
    return SPEED_BY_CATEGORY.get(category.strip().upper(), DEFAULT_SPEED)


def _minutes_per_hop(category: str) -> int:
    return MINUTES_PER_HOP.get(category.strip().upper(), DEFAULT_MINUTES_PER_HOP)


def _travel_estimate(category: str, hops: int) -> int:
    """Minutos estimados de viaje para `hops` tramos."""
    return max(_minutes_per_hop(category) * max(hops, 1), 15)


def _estimate_distance(minutes: int, category: str) -> float:
    return round(max(minutes, 0) / 60 * _speed(category), 1)


def _index_of(names: list[str], wanted: str) -> int:
    target = wanted.casefold()
    for index, name in enumerate(names):
        if name.casefold() == target:
            return index
    for index, name in enumerate(names):
        if target and target in name.casefold():
            return index
    return 0


def _build_trip(
    feed: gtfs.Feed,
    event: StopEvent,
    origin: Station,
    destination: Station | None,
    day: date,
    *,
    budget: CallBudget | None = None,
) -> gtfs.Trip | None:
    """Convierte un tren real en un `Trip` con paradas y tramos.

    En RIS la ruta de salida (`ppth` del `dp`) es el trayecto desde la estación
    de salida y la ruta de llegada (`ppth` del `ar`) el trayecto hasta ella.
    """
    departure = event.departure
    if departure is None:
        return None

    forward = tuple(event.path[:DB_API_MAX_STOPS])
    train_origin = event.route_origin
    route = forward
    arrival_hint: datetime | None = None
    travel = _travel_estimate(event.category, len(forward) - 1)

    if destination is not None:
        destination_name = destination.name
        target = _resolve_station_safe(destination_name)
        if target is not None:
            # Estimación gruesa de la hora de llegada según el tipo de tren:
            # sirve para elegir la hora del plan que hay que consultar.
            guess = departure + timedelta(minutes=travel)
            found = _lookup_at(
                target,
                day,
                guess.hour,
                event.category,
                event.number,
                train_origin,
                expected=guess,
                budget=budget,
            )
            if found is not None:
                arrival_hint = found.arrival or found.departure
                if len(found.arrival_path) >= 2:
                    route = tuple(found.arrival_path[:DB_API_MAX_STOPS])
    else:
        destination_name = forward[-1] if forward else ""

    if len(route) < 2:
        return None

    origin_index = _index_of(list(route), origin.name)
    if origin_index >= len(route) - 1:
        # El tren termina aquí: no es una salida desde el origen.
        return None

    destination_index = _index_of(list(route), destination_name)
    if destination_index <= origin_index:
        destination_index = len(route) - 1
        destination_name = route[-1]

    if arrival_hint is None:
        arrival_hint = departure + timedelta(minutes=travel)

    details = _stop_details(
        route,
        day,
        departure,
        arrival_hint,
        event.category,
        event.number,
        train_origin,
        budget=budget,
    )

    trip_stops: list[gtfs.TripStop] = []
    stamps: list[tuple[datetime, datetime]] = []
    for index, name in enumerate(route):
        info = details.get(name, {})
        arrival = info.get("arrival") or departure
        stop_departure = info.get("departure") or arrival
        if index == origin_index:
            arrival = stop_departure = departure
        if index == destination_index:
            arrival = arrival_hint
        stamps.append((arrival, stop_departure))

    # La API puede devolver horas inconsistentes entre estaciones (o de días
    # distintos): se fuerza una secuencia monótona para que los tramos
    # tengan duraciones sensatas.
    for index in range(1, len(stamps)):
        previous_departure = stamps[index - 1][1]
        arrival, stop_departure = stamps[index]
        if arrival < previous_departure:
            arrival = previous_departure
        if stop_departure < arrival:
            stop_departure = arrival
        stamps[index] = (arrival, stop_departure)

    for index, name in enumerate(route):
        arrival, stop_departure = stamps[index]
        trip_stops.append(
            gtfs.TripStop(
                stop_id=name,
                stop_name=name,
                arrival=arrival.strftime("%H:%M:%S"),
                departure=stop_departure.strftime("%H:%M:%S"),
                sequence=index + 1,
            )
        )

    duration = max(
        gtfs.duration_minutes(
            trip_stops[origin_index].departure, trip_stops[destination_index].arrival
        ),
        1,
    )
    route_name = event.train or event.line or event.category or "Zug"
    trip_id = (
        f"DB_{event.eva}_{departure:%y%m%d%H%M}_{event.category}{event.number}".replace(" ", "")
    )

    trip = gtfs.Trip(
        trip_id=trip_id,
        route_id=event.category or "DB",
        route_name=route_name,
        headsign=destination_name,
        origin_stop=route[origin_index],
        destination_stop=route[destination_index],
        departure=trip_stops[origin_index].departure,
        arrival=trip_stops[destination_index].arrival,
        duration_minutes=duration,
        price=gtfs.compute_trip_price(duration, len(trip_stops)),
        stops=trip_stops,
        train_number=event.number,
        live={
            "delay_minutes": event.delay_minutes,
            "platform": event.platform,
            "planned_platform": None,
            "cancelled": event.cancelled,
            "stops": {
                name: {
                    "delay_minutes": info.get("delay_minutes", 0),
                    "platform": info.get("platform"),
                    "cancelled": info.get("cancelled", False),
                }
                for name, info in details.items()
            },
        },
    )

    for stop in trip_stops:
        feed.stops.setdefault(
            stop.stop_name,
            gtfs.Stop(stop_id=stop.stop_name, name=stop.stop_name),
        )

    for i in range(len(trip_stops)):
        for j in range(i + 1, len(trip_stops)):
            origin_stop, destination_stop = trip_stops[i], trip_stops[j]
            minutes = gtfs.duration_minutes(origin_stop.departure, destination_stop.arrival)
            distance = _estimate_distance(minutes, event.category)
            segment = gtfs.Segment(
                id=f"{trip_id}:{origin_stop.sequence}:{destination_stop.sequence}",
                trip_id=trip_id,
                origin_sequence=origin_stop.sequence,
                destination_sequence=destination_stop.sequence,
                origin_stop=origin_stop.stop_name,
                destination_stop=destination_stop.stop_name,
                departure=origin_stop.departure,
                arrival=destination_stop.arrival,
                duration_minutes=minutes,
                distance_km=distance,
                price=gtfs.compute_segment_price(distance),
            )
            feed.segments[segment.id] = segment
            feed.segments_by_trip.setdefault(trip_id, []).append(segment)

    feed.trips[trip_id] = trip
    return trip


def _name_matches(left: str, right: str) -> bool:
    """Compara nombres de estación de forma tolerante (mayúsculas/recortes)."""
    a, b = left.casefold().strip(), right.casefold().strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def search(
    origin: Station,
    destination: Station | None,
    day: date,
    time_from: int | None,
    time_to: int | None,
    *,
    time_type: str = "departure",
) -> tuple[gtfs.Feed, list[dict[str, Any]]]:
    """Busca conexiones reales y devuelve el feed con los viajes encontrados."""
    feed = gtfs.Feed()
    results: list[dict[str, Any]] = []
    budget = CallBudget()

    window = departures(
        origin, day, time_from, time_to, time_type=time_type, budget=budget
    )
    if destination is not None:
        window = [
            event
            for event in window
            if any(_name_matches(destination.name, name) for name in event.path)
        ]

    for event in window[:DB_API_MAX_TRAINS]:
        if event.cancelled:
            continue
        trip = _build_trip(feed, event, origin, destination, day, budget=budget)
        if trip is None:
            continue
        segment = feed.segment_between(trip.trip_id, trip.origin_stop, trip.destination_stop)
        if segment is None:
            continue
        state = inventory.availability_for_segment(feed, segment.id)
        results.append(
            {
                "trip_id": trip.trip_id,
                "route_name": trip.route_name,
                "train": train_label(trip.route_name),
                "train_number": trip.train_number,
                "train_display": trip.train_display,
                "origin_stop": segment.origin_stop,
                "destination_stop": segment.destination_stop,
                "departure": segment.departure,
                "arrival": segment.arrival,
                "duration_minutes": segment.duration_minutes,
                "distance_km": segment.distance_km,
                "price": segment.price,
                "total_seats": len(state) or 50,
                "available_seats": inventory.count_available(state),
                "segment_id": segment.id,
            }
        )

    results.sort(key=lambda row: row["departure"])
    return feed, results
