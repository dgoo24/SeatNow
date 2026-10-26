"""Echtzeit-Fahrplandaten der DB API (API Marketplace) für die Streamlit-App.

Dieses Modul bildet dieselbe ``handle()``-Schnittstelle wie ``demo_backend`` ab,
holt Bahnhöfe und Fahrpläne aber live von der Deutschen Bahn:

* **StaDa v2** – Bahnhofskatalog (einmalig als ``data/db_stations.json``).
* **Timetables v1** – Soll-Fahrplan je Bahnhof und Stunde.

Sitzplätze, Preise und Buchungen bleiben simuliert, weil die DB API keine
Sitzplatzverfügbarkeit oder Preise veröffentlicht. Alle übrigen Endpunkte
(Login, Buchung, Check-in, Admin) werden an ``demo_backend`` delegiert.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests

import demo_backend as demo

BERLIN = demo.BERLIN
DEFAULT_MARKETPLACE = "https://apis.deutschebahn.com/db-api-marketplace/apis"
STATION_FILE = Path(__file__).with_name("data") / "db_stations.json"

_CLIENT_ID: str | None = None
_API_KEY: str | None = None
_MARKETPLACE: str = DEFAULT_MARKETPLACE

_PLAN_CACHE: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}
_PLAN_TTL_TODAY = 180
_PLAN_TTL_FUTURE = 1800
_EVA_PREF: dict[str, str] = {}

_STATIONS: list[dict[str, Any]] | None = None
_ALIASES = {
    "frankfurt": "Frankfurt (Main) Hbf",
    "frankfurt hbf": "Frankfurt (Main) Hbf",
    "frankfurt main hbf": "Frankfurt (Main) Hbf",
    "frankfurt main": "Frankfurt (Main) Hbf",
    "munich": "München Hbf",
    "muenchen": "München Hbf",
    "cologne": "Köln Hbf",
    "koeln": "Köln Hbf",
    "cologne central": "Köln Hbf",
    "nuremberg": "Nürnberg Hbf",
    "nuernberg": "Nürnberg Hbf",
    "hanover": "Hannover Hbf",
    "brunswick": "Braunschweig Hbf",
    "freiburg im breisgau": "Freiburg(Breisgau) Hbf",
    "freiburg breisgau": "Freiburg(Breisgau) Hbf",
    "freiburg": "Freiburg(Breisgau) Hbf",
    "halle": "Halle(Saale)Hbf",
    "kassel": "Kassel-Wilhelmshöhe",
}

MAX_CANDIDATES = 40
MAX_RESULTS = 20
MAX_TRIP_HOURS = 12


def configure(client_id: str | None, api_key: str | None, base: str | None = None) -> bool:
    """Hinterlegt die Zugangsdaten. Gibt ``True`` zurück, wenn beide gesetzt sind."""
    global _CLIENT_ID, _API_KEY, _MARKETPLACE
    _CLIENT_ID = (client_id or "").strip() or None
    _API_KEY = (api_key or "").strip() or None
    _MARKETPLACE = (base or DEFAULT_MARKETPLACE).rstrip("/")
    return bool(_CLIENT_ID and _API_KEY)


def is_configured() -> bool:
    return bool(_CLIENT_ID and _API_KEY)


def _headers() -> dict[str, str]:
    if not is_configured():
        raise demo.DemoError(
            "DB-API-Zugangsdaten fehlen (DB_CLIENT_ID / DB_API_KEY).",
            503,
            "DB_NOT_CONFIGURED",
        )
    return {
        "DB-Client-Id": str(_CLIENT_ID),
        "DB-Api-Key": str(_API_KEY),
        "Accept": "application/xml, application/json",
    }


def _key(value: Any) -> str:
    return demo._key(value)


def _code(name: str) -> str:
    return hashlib.sha1(_key(name).encode("utf-8")).hexdigest()[:6]


def _norm_station(name: Any) -> str:
    text = str(name or "").lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(source, target)
    text = text.replace("hauptbahnhof", "hbf")
    return re.sub(r"[^a-z0-9]+", "", text)


def _is_main_station(name: Any) -> bool:
    norm = _norm_station(name)
    return norm.endswith("hbf") or norm.endswith("centralstation")


def stations() -> list[dict[str, Any]]:
    """Lädt den Bahnhofskatalog (einmal pro Prozess)."""
    global _STATIONS
    if _STATIONS is None:
        try:
            raw = json.loads(STATION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        _STATIONS = [item for item in raw if item.get("name") and item.get("eva")]
    return _STATIONS


def _station_by_name(name: str) -> dict[str, Any] | None:
    target = _norm_station(name)
    for station in stations():
        if _norm_station(station["name"]) == target:
            return station
    return None


def _find_station(value: Any) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    alias = _ALIASES.get(_key(text))
    if alias:
        station = _station_by_name(alias)
        if station:
            return station
    best: tuple[int, int, int, int, int, dict[str, Any]] | None = None
    for index, station in enumerate(stations()):
        score = demo._stop_score(text, station["name"])
        if score is None:
            continue
        candidate = (
            score,
            0 if _is_main_station(station["name"]) else 1,
            int(station.get("category") or 9),
            len(station["name"]),
            index,
            station,
        )
        if best is None or candidate[:5] < best[:5]:
            best = candidate
    return best[5] if best else None


def _stops(params: dict[str, Any]) -> list[dict[str, Any]]:
    query = str(params.get("q") or "").strip()
    try:
        limit = min(max(int(params.get("limit") or 8), 1), 50)
    except (TypeError, ValueError):
        limit = 8
    if len(query) < 2:
        return []
    key = _key(query)
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        norm = _norm_station(name)
        if name and norm not in seen:
            seen.add(norm)
            names.append(name)

    alias = _ALIASES.get(key)
    if alias and _station_by_name(alias):
        add(alias)
    scored: list[tuple[int, int, int, int, str]] = []
    for index, station in enumerate(stations()):
        score = demo._stop_score(query, station["name"])
        if score is None:
            continue
        scored.append(
            (
                score,
                0 if _is_main_station(station["name"]) else 1,
                int(station.get("category") or 9),
                index,
                station["name"],
            )
        )
    scored.sort()
    for *_, name in scored:
        add(name)
    return [{"stopName": name} for name in names[:limit]]


def _nearby(params: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(params.get("lat"))
        lon = float(params.get("lon"))
    except (TypeError, ValueError):
        raise demo.DemoError("Ungültige Koordinaten.", 400, "INVALID_INPUT") from None
    candidates = [s for s in stations() if s.get("lat") is not None and s.get("lon") is not None]
    if not candidates:
        raise demo.DemoError("Keine Bahnhofsdaten verfügbar.", 503, "DB_NOT_CONFIGURED")
    station = min(
        candidates,
        key=lambda item: demo._haversine((lat, lon), (float(item["lat"]), float(item["lon"]))),
    )
    return {
        "stopName": station["name"],
        "latitude": station["lat"],
        "longitude": station["lon"],
        "distanceKm": round(
            demo._haversine((lat, lon), (float(station["lat"]), float(station["lon"]))), 2
        ),
    }


def _parse_pt(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=BERLIN)


def _parse_plan(content: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], ""
    events: list[dict[str, Any]] = []
    for node in root.findall("s"):
        tl = node.find("tl")
        if tl is None:
            continue
        category = (tl.get("c") or "").strip()
        number = (tl.get("n") or "").strip()
        for kind, tag in (("departure", "dp"), ("arrival", "ar")):
            item = node.find(tag)
            if item is None or not item.get("pt"):
                continue
            try:
                when = _parse_pt(str(item.get("pt")))
            except ValueError:
                continue
            events.append(
                {
                    "kind": kind,
                    "category": category,
                    "number": number,
                    "when": when,
                    "platform": item.get("pp") or "",
                    "line": item.get("l") or "",
                    "path": [part for part in (item.get("ppth") or "").split("|") if part],
                }
            )
    return events, root.get("station") or ""


def _fetch_plan(
    eva: str, date_value: dt.date, hour: int
) -> tuple[list[dict[str, Any]], str]:
    url = f"{_MARKETPLACE}/timetables/v1/plan/{eva}/{date_value.strftime('%y%m%d')}/{hour:02d}"
    try:
        response = requests.get(url, headers=_headers(), timeout=25)
    except requests.RequestException as exc:
        raise demo.DemoError(f"DB-API nicht erreichbar: {exc}", 502, "DB_UNAVAILABLE") from exc
    if response.status_code == 404:
        return [], ""
    if response.status_code in (401, 403):
        raise demo.DemoError(
            "Die DB-API hat die Zugangsdaten abgelehnt. Prüfe DB_CLIENT_ID und DB_API_KEY "
            "sowie das Abo für „Timetables“.",
            502,
            "DB_AUTH",
        )
    if response.status_code == 429:
        raise demo.DemoError(
            "Das DB-API-Kontingent ist erschöpft. Bitte versuche es in einigen Minuten erneut.",
            429,
            "DB_RATE_LIMIT",
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise demo.DemoError(f"DB-API-Fehler: {exc}", 502, "DB_UNAVAILABLE") from exc
    return _parse_plan(response.content)


def _plan(station: dict[str, Any], date_value: dt.date, hour: int) -> list[dict[str, Any]]:
    name = str(station["name"])
    key = (name, date_value.isoformat(), hour)
    now = time.time()
    cached = _PLAN_CACHE.get(key)
    if cached:
        ttl = _PLAN_TTL_TODAY if date_value == dt.datetime.now(BERLIN).date() else _PLAN_TTL_FUTURE
        if now - cached[0] < ttl:
            return cached[1]
    evas = [str(item) for item in (station.get("evas") or [station["eva"]]) if item]
    preferred = _EVA_PREF.get(name)
    if preferred and preferred in evas:
        evas = [preferred] + [item for item in evas if item != preferred]
    target = _norm_station(name)
    events: list[dict[str, Any]] = []
    best: tuple[int, int, str, list[dict[str, Any]]] | None = None
    for eva in evas:
        found, label = _fetch_plan(eva, date_value, hour)
        if not found:
            continue
        label_norm = _norm_station(label)
        match = 0 if label_norm == target else 1 if target[:8] == label_norm[:8] else 2
        candidate = (match, -len(found), eva, found)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
        if match == 0:
            break
    if best is not None:
        events = best[3]
        _EVA_PREF[name] = best[2]
    _PLAN_CACHE[key] = (now, events)
    return events


def _path_has(path: list[str], station_name: str) -> bool:
    target = _norm_station(station_name)
    if not target:
        return False
    for item in path:
        candidate = _norm_station(item)
        if not candidate:
            continue
        if candidate == target or candidate.startswith(target) or target.startswith(candidate):
            return True
    return False


def _find_arrival(
    destination: dict[str, Any],
    origin_name: str,
    departure: dict[str, Any],
) -> dict[str, Any] | None:
    dep = departure["when"]
    limit = dep + dt.timedelta(hours=MAX_TRIP_HOURS)
    earliest = dep + dt.timedelta(minutes=5)
    best: dict[str, Any] | None = None
    cursor = dep.replace(minute=0, second=0, microsecond=0)
    seen: set[tuple[dt.date, int]] = set()
    while cursor <= limit:
        key = (cursor.date(), cursor.hour)
        if key not in seen:
            seen.add(key)
            for event in _plan(destination, cursor.date(), cursor.hour):
                if event["kind"] != "arrival":
                    continue
                if event["category"] != departure["category"]:
                    continue
                if event["number"] != departure["number"]:
                    continue
                if not _path_has(event["path"], origin_name):
                    continue
                if not earliest <= event["when"] <= limit:
                    continue
                if best is None or event["when"] < best["when"]:
                    best = event
        cursor += dt.timedelta(hours=1)
    return best


def _metrics(origin: dict[str, Any], destination: dict[str, Any]) -> tuple[int, int]:
    try:
        km = round(
            demo._haversine(
                (float(origin["lat"]), float(origin["lon"])),
                (float(destination["lat"]), float(destination["lon"])),
            )
            * 1.12
        )
    except (KeyError, TypeError, ValueError):
        km = 200
    km = max(25, km)
    price = max(490, round((490 + km * 8.5) / 10.0) * 10)
    return km, price


def _build_trip(
    origin: dict[str, Any],
    destination: dict[str, Any],
    date_value: dt.date,
    departure: dict[str, Any],
    arrival: dict[str, Any],
) -> dict[str, Any]:
    dep = departure["when"]
    arr = arrival["when"]
    minutes = max(1, int((arr - dep).total_seconds() // 60))
    km, price = _metrics(origin, destination)
    seed = demo._seed(origin["eva"], destination["eva"], dep.isoformat())
    index = seed % 100000
    trip_id = (
        f"demo-{date_value.isoformat()}-{index}-"
        f"{_code(origin['name'])}-{_code(destination['name'])}"
    )
    cached = demo._TRIPS.get(trip_id)
    if cached:
        return cached
    route_name = departure["category"] or "Zug"
    stops = [{"stopName": origin["name"], "departureTime": dep.isoformat()}]
    for name in departure["path"]:
        if _norm_station(name) == _norm_station(destination["name"]):
            break
        if name and name != origin["name"]:
            stops.append({"stopName": name})
    stops.append({"stopName": destination["name"], "arrivalTime": arr.isoformat()})
    segment = {
        "id": f"{trip_id}-s1",
        "from": origin["name"],
        "to": destination["name"],
        "departureTime": dep.isoformat(),
        "arrivalTime": arr.isoformat(),
        "durationMinutes": minutes,
        "distanceKm": km,
        "price": price,
    }
    trip = {
        "id": trip_id,
        "routeName": route_name,
        "trainNumber": departure["number"],
        "originStop": origin["name"],
        "destinationStop": destination["name"],
        "departureTime": dep.isoformat(),
        "arrivalTime": arr.isoformat(),
        "durationMinutes": minutes,
        "segments": [segment],
        "stops": stops,
    }
    demo._TRIPS[trip_id] = trip
    return trip


def _parse_clock(value: Any) -> dt.time | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.time.fromisoformat(text[:5])
    except ValueError:
        return None


def _search(params: dict[str, Any]) -> list[dict[str, Any]]:
    origin = _find_station(params.get("origin"))
    destination = _find_station(params.get("destination"))
    if not origin or not destination or origin["eva"] == destination["eva"]:
        return []

    today = dt.datetime.now(BERLIN).date()
    try:
        date_value = dt.date.fromisoformat(str(params.get("date") or today.isoformat()))
    except ValueError:
        date_value = today
    if date_value > today + dt.timedelta(days=1):
        raise demo.DemoError(
            "Für dieses Datum liefert die DB-API noch keine Fahrplandaten. "
            "Der Fahrplan ist nur für heute und die nächsten ~24 Stunden verfügbar.",
            400,
            "NO_DATA",
        )
    if date_value < today:
        return []

    from_time = _parse_clock(params.get("timeFrom"))
    to_time = _parse_clock(params.get("timeTo"))
    mode = "arrival" if params.get("timeType") == "arrival" else "departure"

    now = dt.datetime.now(BERLIN)
    if date_value == today:
        if mode == "arrival":
            start_hour = max(0, now.hour - 4)
        else:
            start_hour = max(0, (from_time.hour if from_time else now.hour))
    else:
        start_hour = 0

    candidates: list[dict[str, Any]] = []
    seen_departures: set[tuple[str, str, dt.datetime]] = set()
    for hour in range(start_hour, 24):
        for event in _plan(origin, date_value, hour):
            if event["kind"] != "departure":
                continue
            if event["when"].date() != date_value:
                continue
            if not _path_has(event["path"], destination["name"]):
                continue
            identity = (event["category"], event["number"], event["when"])
            if identity in seen_departures:
                continue
            seen_departures.add(identity)
            candidates.append(event)
        if len(candidates) >= MAX_CANDIDATES:
            break

    results: list[dict[str, Any]] = []
    seen_trips: set[str] = set()
    for departure in candidates[:MAX_CANDIDATES]:
        arrival = _find_arrival(destination, origin["name"], departure)
        if not arrival:
            continue
        clock = arrival["when"].time() if mode == "arrival" else departure["when"].time()
        if from_time and clock < from_time:
            continue
        if to_time and clock > to_time:
            continue
        trip = _build_trip(origin, destination, date_value, departure, arrival)
        if trip["id"] in seen_trips:
            continue
        seen_trips.add(trip["id"])
        results.append(trip)

    results.sort(key=lambda trip: trip["departureTime"])
    rows = [demo._row(trip) for trip in results[:MAX_RESULTS]]
    if not rows and date_value > today:
        raise demo.DemoError(
            "Für dieses Datum liegen noch keine Fahrplandaten vor. "
            "Die DB-API liefert Fahrpläne nur für heute und die nächsten ~24 Stunden.",
            400,
            "NO_DATA",
        )
    return rows


def _stats() -> dict[str, Any]:
    return {
        "trips": 0,
        "segments": 0,
        "seats": 0,
        "lastGtfsIngestedAt": None,
        "source": "DB API Marketplace",
        "stations": len(stations()),
    }


def handle(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    token: str | None = None,
) -> Any:
    clean_path = str(path).split("?")[0].rstrip("/") or "/"
    query = dict(params or {})
    if method.upper() == "GET":
        if clean_path == "/health":
            return {
                "status": "ok",
                "payments": "mock",
                "mail": "disabled",
                "mode": "db-api",
                "source": "DB API Marketplace",
            }
        if clean_path == "/api/trains/stats":
            return _stats()
        if clean_path == "/api/trains/stops":
            return _stops(query)
        if clean_path == "/api/trains/stops/nearby":
            return _nearby(query)
        if clean_path == "/api/trains/search":
            return _search(query)
    return demo.handle(method, path, params=params, json_body=json_body, token=token)
