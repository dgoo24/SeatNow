from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import re
import threading
import uuid
from typing import Any
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
HOLD_SECONDS = 600
LOCK = threading.RLock()

STATIONS: dict[str, tuple[float, float]] = {
    "Berlin Hbf": (52.5251, 13.3694),
    "Berlin Südkreuz": (52.4755, 13.3654),
    "Potsdam Hbf": (52.3915, 13.0677),
    "Hamburg Hbf": (53.5526, 10.0067),
    "Hamburg-Altona": (53.5520, 9.9350),
    "Kiel Hbf": (54.3147, 10.1319),
    "Lübeck Hbf": (53.8676, 10.6697),
    "Rostock Hbf": (54.0782, 12.1310),
    "Bremen Hbf": (53.0833, 8.8137),
    "Hannover Hbf": (52.3767, 9.7411),
    "Braunschweig Hbf": (52.2525, 10.5406),
    "Wolfsburg Hbf": (52.4289, 10.7877),
    "Magdeburg Hbf": (52.1305, 11.6270),
    "Göttingen": (51.5367, 9.9265),
    "Kassel-Wilhelmshöhe": (51.3128, 9.4465),
    "Fulda": (50.5549, 9.6845),
    "Bielefeld Hbf": (52.0294, 8.5318),
    "Osnabrück Hbf": (52.2729, 8.0612),
    "Münster(Westf)Hbf": (51.9560, 7.6353),
    "Dortmund Hbf": (51.5179, 7.4593),
    "Essen Hbf": (51.4512, 7.0137),
    "Düsseldorf Hbf": (51.2203, 6.7934),
    "Köln Hbf": (50.9430, 6.9585),
    "Aachen Hbf": (50.7681, 6.0915),
    "Koblenz Hbf": (50.3509, 7.5888),
    "Mainz Hbf": (50.0013, 8.2588),
    "Wiesbaden Hbf": (50.0711, 8.2446),
    "Frankfurt(Main)Hbf": (50.1071, 8.6638),
    "Frankfurt Flughafen": (50.0520, 8.5700),
    "Darmstadt Hbf": (49.8728, 8.6512),
    "Mannheim Hbf": (49.4796, 8.4691),
    "Heidelberg Hbf": (49.4035, 8.6756),
    "Karlsruhe Hbf": (48.9935, 8.4011),
    "Stuttgart Hbf": (48.7839, 9.1817),
    "Ulm Hbf": (48.3990, 9.9825),
    "Augsburg Hbf": (48.3655, 10.8863),
    "München Hbf": (48.1402, 11.5601),
    "Regensburg Hbf": (49.0115, 12.0995),
    "Nürnberg Hbf": (49.4457, 11.0824),
    "Würzburg Hbf": (49.8016, 9.9358),
    "Erfurt Hbf": (50.9725, 11.0384),
    "Halle(Saale)Hbf": (51.4776, 11.9877),
    "Leipzig Hbf": (51.3455, 12.3821),
    "Dresden Hbf": (51.0400, 13.7320),
    "Chemnitz Hbf": (50.8393, 12.9303),
    "Freiburg(Breisgau) Hbf": (47.9977, 7.8418),
    "Basel Bad Bf": (47.5673, 7.6074),
    "Saarbrücken Hbf": (49.2412, 6.9905),
    "Trier Hbf": (49.7602, 6.6439),
}

_ALIASES = {
    "frankfurt": "Frankfurt(Main)Hbf",
    "frankfurt hbf": "Frankfurt(Main)Hbf",
    "frankfurt main hbf": "Frankfurt(Main)Hbf",
    "frankfurt main": "Frankfurt(Main)Hbf",
    "munich": "München Hbf",
    "muenchen": "München Hbf",
    "munich hbf": "München Hbf",
    "cologne": "Köln Hbf",
    "koeln": "Köln Hbf",
    "cologne hbf": "Köln Hbf",
    "nuremberg": "Nürnberg Hbf",
    "nuernberg": "Nürnberg Hbf",
    "hamburg": "Hamburg Hbf",
    "berlin": "Berlin Hbf",
    "freiburg": "Freiburg(Breisgau) Hbf",
    "freiburg hbf": "Freiburg(Breisgau) Hbf",
    "freiburg breisgau hbf": "Freiburg(Breisgau) Hbf",
    "halle": "Halle(Saale)Hbf",
    "muenster": "Münster(Westf)Hbf",
    "kassel": "Kassel-Wilhelmshöhe",
    "cologne central": "Köln Hbf",
}

_SLOTS = [
    (6, 12),
    (7, 48),
    (9, 21),
    (10, 55),
    (12, 30),
    (14, 4),
    (15, 37),
    (17, 11),
    (18, 46),
    (20, 20),
    (21, 54),
]

_USERS: dict[str, dict[str, Any]] = {
    "demo@trenes.local": {
        "id": "u-demo",
        "email": "demo@trenes.local",
        "fullName": "Demo Reisende:r",
        "role": "USER",
        "password": "demo1234",
    },
    "admin@trenes.local": {
        "id": "u-admin",
        "email": "admin@trenes.local",
        "fullName": "Admin",
        "role": "ADMIN",
        "password": "admin1234",
    },
}
_BOOKINGS: dict[str, dict[str, Any]] = {}
_SEAT_OVERRIDES: dict[tuple[str, str, str], tuple[str, float | None]] = {}
_TRIPS: dict[str, dict[str, Any]] = {}


class DemoError(Exception):
    def __init__(self, message: str, status: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _key(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(source, target)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _code(name: str) -> str:
    return hashlib.sha1(_key(name).encode("utf-8")).hexdigest()[:6]


def _seed(*parts: Any) -> int:
    raw = "|".join(str(part) for part in parts)
    return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8], 16)


def _find_stop(value: Any) -> str | None:
    key = _key(value)
    if not key:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    for display in STATIONS:
        if _key(display) == key:
            return display
    for display in STATIONS:
        display_key = _key(display)
        if key in display_key or display_key in key:
            return display
    return None


def _by_code(code: str) -> str | None:
    for display in STATIONS:
        if _code(display) == code:
            return display
    return None


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(BERLIN).isoformat()


def _parse_date(value: Any) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return dt.datetime.now(BERLIN).date()


def _parse_clock(value: Any) -> dt.time | None:
    if not value:
        return None
    try:
        hours, minutes = str(value).split(":")[:2]
        return dt.time(int(hours), int(minutes))
    except (TypeError, ValueError):
        return None


def _train_label(trip: dict[str, Any]) -> str:
    return f"{trip.get('routeName') or 'ICE'} {trip.get('trainNumber')}"


def _route_metrics(origin: str, destination: str) -> tuple[int, int, int]:
    km = round(_haversine(STATIONS[origin], STATIONS[destination]) * 1.12)
    km = max(25, km)
    minutes = max(35, round(km / 115 * 60) + 12)
    price = max(490, round((490 + km * 8.5) / 10.0) * 10)
    return km, minutes, price


def _route_name(km: int, seed: int) -> str:
    if km > 300:
        return "ICE"
    if km > 130:
        return "ICE" if seed % 3 else "IC"
    return "RE" if seed % 2 else "IC"


def _build_trip(origin: str, destination: str, date_value: dt.date, index: int) -> dict[str, Any]:
    trip_id = f"demo-{date_value.isoformat()}-{index}-{_code(origin)}-{_code(destination)}"
    cached = _TRIPS.get(trip_id)
    if cached:
        return cached

    km, minutes, price = _route_metrics(origin, destination)
    seed = _seed(trip_id)
    hours, mins = _SLOTS[index % len(_SLOTS)]
    jitter = seed % 9 - 4
    departure = dt.datetime.combine(date_value, dt.time(hours, mins), tzinfo=BERLIN)
    departure += dt.timedelta(minutes=jitter)
    minutes += seed % 5
    arrival = departure + dt.timedelta(minutes=minutes)
    route_name = _route_name(km, seed)
    train_number = 100 + seed % 800
    segment_id = f"{trip_id}-s1"
    segment = {
        "id": segment_id,
        "from": origin,
        "to": destination,
        "departureTime": departure.isoformat(),
        "arrivalTime": arrival.isoformat(),
        "durationMinutes": minutes,
        "distanceKm": km,
        "price": price,
    }
    trip = {
        "id": trip_id,
        "routeName": route_name,
        "trainNumber": train_number,
        "originStop": origin,
        "destinationStop": destination,
        "departureTime": departure.isoformat(),
        "arrivalTime": arrival.isoformat(),
        "durationMinutes": minutes,
        "segments": [segment],
        "stops": [
            {"stopName": origin, "departureTime": departure.isoformat()},
            {"stopName": destination, "arrivalTime": arrival.isoformat()},
        ],
    }
    _TRIPS[trip_id] = trip
    return trip


def _trip_from_id(trip_id: str) -> dict[str, Any]:
    cached = _TRIPS.get(trip_id)
    if cached:
        return cached
    match = re.fullmatch(r"demo-(\d{4}-\d{2}-\d{2})-(\d+)-([0-9a-f]{6})-([0-9a-f]{6})", trip_id)
    if not match:
        raise DemoError("Fahrt nicht gefunden.", 404, "NOT_FOUND")
    date_value = _parse_date(match.group(1))
    index = int(match.group(2))
    origin = _by_code(match.group(3))
    destination = _by_code(match.group(4))
    if not origin or not destination:
        raise DemoError("Fahrt nicht gefunden.", 404, "NOT_FOUND")
    return _build_trip(origin, destination, date_value, index)


def _availability(trip: dict[str, Any], segment_id: str) -> list[dict[str, Any]]:
    segment = next((item for item in trip["segments"] if item["id"] == segment_id), None)
    if not segment:
        raise DemoError("Teilstrecke nicht gefunden.", 404, "NOT_FOUND")

    seats: list[dict[str, Any]] = []
    for car in range(1, 4):
        for number in range(1, 21):
            seat_id = f"{trip['id']}-{car}-{number:02d}"
            override = _SEAT_OVERRIDES.get((trip["id"], segment_id, seat_id))
            if override:
                status = override[0]
            else:
                bucket = _seed("seat", trip["id"], segment_id, car, number) % 100
                if bucket < 58:
                    status = "AVAILABLE"
                elif bucket < 80:
                    status = "BOOKED"
                else:
                    status = "HELD"
            reservations = []
            if status != "AVAILABLE":
                reservations = [
                    {"from": segment["from"], "to": segment["to"], "status": status}
                ]
            seats.append(
                {
                    "seatId": seat_id,
                    "carNumber": car,
                    "seatNumber": f"{number:02d}",
                    "status": status,
                    "reservations": reservations,
                }
            )
    return seats


def _free_count(trip: dict[str, Any], segment: dict[str, Any]) -> int:
    seats = _availability(trip, segment["id"])
    return sum(1 for seat in seats if seat["status"] == "AVAILABLE")


def _row(trip: dict[str, Any]) -> dict[str, Any]:
    segment = trip["segments"][0]
    return {
        "id": trip["id"],
        "trainNumber": trip["trainNumber"],
        "routeName": trip["routeName"],
        "originStop": trip["originStop"],
        "destinationStop": trip["destinationStop"],
        "departureTime": trip["departureTime"],
        "arrivalTime": trip["arrivalTime"],
        "durationMinutes": trip["durationMinutes"],
        "price": segment["price"],
        "availableSeats": _free_count(trip, segment),
        "segmentId": segment["id"],
    }


def _search_day(
    origin: str,
    destination: str,
    date_value: dt.date,
    from_time: dt.time | None,
    to_time: dt.time | None,
    mode: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(len(_SLOTS)):
        trip = _build_trip(origin, destination, date_value, index)
        departure = dt.datetime.fromisoformat(trip["departureTime"]).time()
        arrival = dt.datetime.fromisoformat(trip["arrivalTime"]).time()
        clock = arrival if mode == "arrival" else departure
        if from_time and clock < from_time:
            continue
        if to_time and clock > to_time:
            continue
        rows.append(_row(trip))
    return rows


def _search(params: dict[str, Any]) -> list[dict[str, Any]]:
    origin = _find_stop(params.get("origin"))
    destination = _find_stop(params.get("destination"))
    if not origin or not destination or origin == destination:
        return []

    date_value = _parse_date(params.get("date"))
    from_time = _parse_clock(params.get("timeFrom"))
    to_time = _parse_clock(params.get("timeTo"))
    mode = "arrival" if params.get("timeType") == "arrival" else "departure"
    rows = _search_day(origin, destination, date_value, from_time, to_time, mode)
    if not rows:
        next_day = date_value + dt.timedelta(days=1)
        rows = _search_day(origin, destination, next_day, None, None, mode)[:4]
    try:
        limit = int(params.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    return rows[:limit]


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in user.items() if key != "password"}


def _token_for(user: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(user["email"].encode("utf-8")).decode("ascii")
    return f"demo-{encoded}"


def _user_from_token(token: str | None) -> dict[str, Any] | None:
    if not token or not str(token).startswith("demo-"):
        return None
    payload = str(token)[5:]
    try:
        email = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return _USERS.get(email.lower())


def _auth(token: str | None) -> dict[str, Any]:
    user = _user_from_token(token)
    if not user:
        raise DemoError("Bitte melde dich an.", 401, "UNAUTHORIZED")
    return user


def _purge_expired() -> None:
    now = _now()
    for (trip_id, segment_id, seat_id), (status, expires) in list(_SEAT_OVERRIDES.items()):
        if status == "HELD" and expires and expires <= now.timestamp():
            del _SEAT_OVERRIDES[(trip_id, segment_id, seat_id)]
    for booking in _BOOKINGS.values():
        if booking.get("status") != "PENDING_PAYMENT" or not booking.get("expiresAt"):
            continue
        try:
            expires = dt.datetime.fromisoformat(str(booking["expiresAt"]))
        except ValueError:
            continue
        if expires <= now:
            booking["status"] = "CANCELLED"
            booking["failureReason"] = "Reservierung abgelaufen"
            booking["expiresAt"] = None
            seat = booking.get("seat") or {}
            segment = booking.get("segment") or {}
            trip = booking.get("trip") or {}
            _SEAT_OVERRIDES.pop(
                (trip.get("id"), segment.get("id"), seat.get("seatId")), None
            )


def _booking_or_404(booking_id: str, user: dict[str, Any]) -> dict[str, Any]:
    booking = _BOOKINGS.get(booking_id)
    if not booking:
        raise DemoError("Buchung nicht gefunden.", 404, "NOT_FOUND")
    if booking["user"]["email"] != user["email"] and user.get("role") != "ADMIN":
        raise DemoError("Buchung nicht gefunden.", 404, "NOT_FOUND")
    return booking


def _qr(booking: dict[str, Any]) -> str:
    segment = booking.get("segment") or {}
    seat = booking.get("seat") or {}
    payload = {
        "booking": booking["id"],
        "route": f"{segment.get('from')} -> {segment.get('to')}",
        "car": seat.get("carNumber"),
        "seat": seat.get("seatNumber"),
        "train": (booking.get("trip") or {}).get("routeName"),
    }
    return "SEATNOW|" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _new_booking(
    trip: dict[str, Any],
    segment: dict[str, Any],
    seat: dict[str, Any],
    user: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    now = _now()
    booking = {
        "id": str(uuid.uuid4()),
        "status": status,
        "totalAmount": segment["price"],
        "createdAt": _iso(now),
        "expiresAt": _iso(now + dt.timedelta(seconds=HOLD_SECONDS))
        if status == "PENDING_PAYMENT"
        else None,
        "isCheckedIn": False,
        "checkedInAt": None,
        "failureReason": None,
        "qrPayload": None,
        "trip": {
            "id": trip["id"],
            "routeName": trip["routeName"],
            "trainNumber": trip["trainNumber"],
            "originStop": trip["originStop"],
            "destinationStop": trip["destinationStop"],
            "departureTime": trip["departureTime"],
            "arrivalTime": trip["arrivalTime"],
        },
        "segment": dict(segment),
        "seat": {
            "seatId": seat["seatId"],
            "carNumber": seat["carNumber"],
            "seatNumber": seat["seatNumber"],
        },
        "user": _public_user(user),
    }
    if status == "CONFIRMED":
        booking["qrPayload"] = _qr(booking)
    _BOOKINGS[booking["id"]] = booking
    override_status = "HELD" if status == "PENDING_PAYMENT" else "BOOKED"
    expires = now.timestamp() + HOLD_SECONDS if status == "PENDING_PAYMENT" else None
    _SEAT_OVERRIDES[(trip["id"], segment["id"], seat["seatId"])] = (override_status, expires)
    return booking


def _login(body: dict[str, Any]) -> dict[str, Any]:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    user = _USERS.get(email)
    if not user or user.get("password") != password:
        raise DemoError("E-Mail oder Passwort ist falsch.", 401, "INVALID_CREDENTIALS")
    return {"token": _token_for(user), "user": _public_user(user)}


def _register(body: dict[str, Any]) -> dict[str, Any]:
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if "@" not in email or len(password) < 6:
        raise DemoError("E-Mail und Passwort (min. 6 Zeichen) prüfen.", 400, "INVALID_INPUT")
    if email in _USERS:
        raise DemoError("Diese E-Mail ist bereits registriert.", 409, "EMAIL_TAKEN")
    user = {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "email": email,
        "fullName": str(body.get("fullName") or email.split("@")[0]),
        "role": "USER",
        "password": password,
    }
    _USERS[email] = user
    return {"token": _token_for(user), "user": _public_user(user)}


def _stops(params: dict[str, Any]) -> list[dict[str, Any]]:
    query = _key(params.get("q"))
    try:
        limit = int(params.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    names: list[str] = []
    if query:
        for alias, display in _ALIASES.items():
            if query in alias and display not in names:
                names.append(display)
        for display in STATIONS:
            if query in _key(display) and display not in names:
                names.append(display)
    else:
        names = list(STATIONS)
    names.sort(key=lambda name: (not _key(name).startswith(query), name))
    return [{"stopName": name} for name in names[:limit]]


def _nearby(params: dict[str, Any]) -> dict[str, Any]:
    try:
        lat = float(params.get("lat"))
        lon = float(params.get("lon"))
    except (TypeError, ValueError):
        raise DemoError("Ungültige Koordinaten.", 400, "INVALID_INPUT")
    name = min(STATIONS, key=lambda item: _haversine((lat, lon), STATIONS[item]))
    return {
        "stopName": name,
        "latitude": STATIONS[name][0],
        "longitude": STATIONS[name][1],
        "distanceKm": round(_haversine((lat, lon), STATIONS[name]), 2),
    }


def _realtime(trip: dict[str, Any]) -> dict[str, Any]:
    segment = trip["segments"][0]
    seed = _seed("live", trip["id"])
    delay = [0, 0, 5, 3, 10, 0, 15][seed % 7]
    planned = dt.datetime.fromisoformat(segment["departureTime"])
    actual = planned + dt.timedelta(minutes=delay)
    platform = 1 + seed % 12
    return {
        "available": True,
        "fetchedAt": _iso(_now()),
        "live": {
            "station": segment["from"],
            "line": _train_label(trip),
            "cancelled": False,
            "delayMinutes": delay,
            "platform": str(platform),
            "plannedPlatform": str(platform),
            "plannedDeparture": planned.strftime("%H:%M"),
            "actualDeparture": actual.strftime("%H:%M"),
            "plannedArrival": dt.datetime.fromisoformat(segment["arrivalTime"]).strftime("%H:%M"),
            "actualArrival": (
                dt.datetime.fromisoformat(segment["arrivalTime"]) + dt.timedelta(minutes=delay)
            ).strftime("%H:%M"),
            "nextStops": [],
        },
    }


def _stats() -> dict[str, Any]:
    today = dt.datetime.now(BERLIN).replace(hour=4, minute=30, second=0, microsecond=0)
    return {
        "trips": 348,
        "segments": 1392,
        "seats": 83520,
        "lastGtfsIngestedAt": today.isoformat(),
    }


def _availability_response(path: str) -> Any:
    match = re.fullmatch(r"/api/trains/([^/]+)/segments/([^/]+)/availability", path)
    if not match:
        return None
    trip = _trip_from_id(match.group(1))
    return _availability(trip, match.group(2))


def _handle_get(path: str, params: dict[str, Any], token: str | None) -> Any:
    if path == "/health":
        return {"status": "ok", "payments": "mock", "mail": "disabled", "mode": "demo"}
    if path == "/api/trains/stats":
        return _stats()
    if path == "/api/trains/stops":
        return _stops(params)
    if path == "/api/trains/stops/nearby":
        return _nearby(params)
    if path == "/api/trains/search":
        return _search(params)
    if path == "/api/auth/me":
        return _public_user(_auth(token))
    if path == "/api/bookings":
        user = _auth(token)
        bookings = [
            booking
            for booking in _BOOKINGS.values()
            if booking["user"]["email"] == user["email"]
        ]
        return sorted(bookings, key=lambda item: item.get("createdAt") or "", reverse=True)
    if path == "/api/admin/pending-bookings":
        user = _auth(token)
        if user.get("role") != "ADMIN":
            raise DemoError("Nur für Administratoren.", 403, "FORBIDDEN")
        return sorted(
            (booking for booking in _BOOKINGS.values() if booking["status"] == "PENDING_PAYMENT"),
            key=lambda item: item.get("createdAt") or "",
            reverse=True,
        )
    if path == "/api/admin/bookings":
        user = _auth(token)
        if user.get("role") != "ADMIN":
            raise DemoError("Nur für Administratoren.", 403, "FORBIDDEN")
        return sorted(
            _BOOKINGS.values(), key=lambda item: item.get("createdAt") or "", reverse=True
        )

    availability = _availability_response(path)
    if availability is not None:
        return availability

    match = re.fullmatch(r"/api/trains/([^/]+)/realtime", path)
    if match:
        return _realtime(_trip_from_id(match.group(1)))

    match = re.fullmatch(r"/api/trains/([^/]+)", path)
    if match:
        return _trip_from_id(match.group(1))

    raise DemoError("Demo-Endpunkt nicht gefunden.", 404, "NOT_FOUND")


def _handle_hold(body: dict[str, Any], token: str | None) -> dict[str, Any]:
    user = _auth(token)
    trip = _trip_from_id(str(body.get("tripId") or ""))
    segment_id = str(body.get("segmentId") or "")
    segment = next((item for item in trip["segments"] if item["id"] == segment_id), None)
    if not segment:
        raise DemoError("Teilstrecke nicht gefunden.", 404, "NOT_FOUND")
    seat_id = str(body.get("seatId") or "")
    seat = next((item for item in _availability(trip, segment_id) if item["seatId"] == seat_id), None)
    if not seat:
        raise DemoError("Sitzplatz nicht gefunden.", 404, "NOT_FOUND")
    if seat["status"] != "AVAILABLE":
        raise DemoError("Dieser Sitzplatz ist nicht mehr frei.", 409, "SEAT_TAKEN")
    booking = _new_booking(trip, segment, seat, user, status="PENDING_PAYMENT")
    return {
        "booking": booking,
        "expiresAt": booking["expiresAt"],
        "ttlSeconds": HOLD_SECONDS,
    }


def _handle_checkout(body: dict[str, Any], token: str | None) -> dict[str, Any]:
    user = _auth(token)
    booking = _booking_or_404(str(body.get("bookingId") or ""), user)
    if booking["status"] != "PENDING_PAYMENT":
        raise DemoError("Diese Reservierung ist nicht mehr offen.", 409, "INVALID_STATE")
    if booking.get("expiresAt") and dt.datetime.fromisoformat(booking["expiresAt"]) <= _now():
        raise DemoError("Die Reservierung ist abgelaufen.", 409, "EXPIRED")
    booking["status"] = "CONFIRMED"
    booking["expiresAt"] = None
    booking["qrPayload"] = _qr(booking)
    trip = booking["trip"]
    segment = booking["segment"]
    seat = booking["seat"]
    _SEAT_OVERRIDES[(trip.get("id"), segment.get("id"), seat.get("seatId"))] = ("BOOKED", None)
    return {"booking": booking, "payment": "mock", "emailSent": False}


def _handle_cancel(path: str, token: str | None) -> Any:
    user = _auth(token)
    match = re.fullmatch(r"/api/bookings/([^/]+)/cancel", path)
    if not match:
        raise DemoError("Demo-Endpunkt nicht gefunden.", 404, "NOT_FOUND")
    booking = _booking_or_404(match.group(1), user)
    if booking.get("isCheckedIn"):
        raise DemoError("Eingecheckte Buchungen können nicht storniert werden.", 409, "CHECKED_IN")
    booking["status"] = "CANCELLED"
    booking["expiresAt"] = None
    booking["qrPayload"] = None
    trip = booking["trip"]
    segment = booking["segment"]
    seat = booking["seat"]
    _SEAT_OVERRIDES.pop((trip.get("id"), segment.get("id"), seat.get("seatId")), None)
    return {"booking": booking}


def _handle_checkin(path: str, body: dict[str, Any], token: str | None) -> Any:
    user = _auth(token)
    match = re.fullmatch(r"/api/bookings/([^/]+)/check-in", path)
    if not match:
        raise DemoError("Demo-Endpunkt nicht gefunden.", 404, "NOT_FOUND")
    booking = _booking_or_404(match.group(1), user)
    if booking["status"] != "CONFIRMED":
        raise DemoError("Nur bestätigte Buchungen können einchecken.", 409, "INVALID_STATE")
    booking["isCheckedIn"] = True
    booking["checkedInAt"] = _iso(_now())
    return {"booking": booking}


def _handle_demo_purchase(body: dict[str, Any]) -> dict[str, Any]:
    trip = _trip_from_id(str(body.get("tripId") or ""))
    segment_id = str(body.get("segmentId") or "")
    segment = next((item for item in trip["segments"] if item["id"] == segment_id), None)
    if not segment:
        raise DemoError("Teilstrecke nicht gefunden.", 404, "NOT_FOUND")
    seats = _availability(trip, segment_id)
    seat = next((item for item in seats if item["status"] == "AVAILABLE"), None)
    if not seat:
        raise DemoError("Keine freien Sitzplätze mehr.", 409, "SOLD_OUT")
    passenger = {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "email": f"demo.passenger.{uuid.uuid4().hex[:4]}@example.com",
        "fullName": "Demo Passagier",
        "role": "USER",
    }
    _new_booking(trip, segment, seat, passenger, status="CONFIRMED")
    return {
        "passengerEmail": passenger["email"],
        "carNumber": seat["carNumber"],
        "seatNumber": seat["seatNumber"],
        "segmentFrom": segment["from"],
        "segmentTo": segment["to"],
    }


def _handle_post(path: str, body: dict[str, Any], token: str | None) -> Any:
    if path == "/api/auth/login":
        return _login(body)
    if path == "/api/auth/register":
        return _register(body)
    if path == "/api/bookings/hold":
        return _handle_hold(body, token)
    if path == "/api/bookings/checkout":
        return _handle_checkout(body, token)
    if path == "/api/demo/purchase":
        return _handle_demo_purchase(body)
    cancel = re.fullmatch(r"/api/bookings/([^/]+)/cancel", path)
    if cancel:
        return _handle_cancel(path, token)
    check_in = re.fullmatch(r"/api/bookings/([^/]+)/check-in", path)
    if check_in:
        return _handle_checkin(path, body, token)
    raise DemoError("Demo-Endpunkt nicht gefunden.", 404, "NOT_FOUND")


def handle(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    token: str | None = None,
) -> Any:
    with LOCK:
        _purge_expired()
        body = json_body if isinstance(json_body, dict) else {}
        query = dict(params or {})
        clean_path = str(path).split("?")[0].rstrip("/") or "/"
        if method.upper() == "GET":
            return _handle_get(clean_path, query, token)
        if method.upper() == "POST":
            return _handle_post(clean_path, body, token)
        raise DemoError("Methode nicht unterstützt.", 405, "METHOD_NOT_ALLOWED")
