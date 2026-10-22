"""Cliente de la Deutsche Bahn Timetables API (db-api-marketplace).

El espejo público de HAFAS (`v6.db.transport.rest`) está caído, así que el
horario real de los trenes se carga desde la API oficial de la DB:

    GET /station/{pattern}           búsqueda de estaciones (EVA)
    GET /plan/{eva}/{yymmdd}/{HH}    horario planificado de una hora
    GET /fchg/{eva}                  cambios en vivo (retraso, vía, cancelación)

Credenciales (`DB_CLIENT_ID` + `DB_API_KEY`) en los secretos de Streamlit Cloud
o en variables de entorno. Límite del marketplace: 60 peticiones/minuto.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seatnow.config import DB_API_BASE_URL, DB_API_TIMEOUT, TIMEZONE

try:
    LOCAL_TZ = ZoneInfo(TIMEZONE)
except ZoneInfoNotFoundError:  # pragma: no cover - sistemas sin tzdata
    LOCAL_TZ = ZoneInfo("Europe/Berlin")

USER_AGENT = "seatnow-streamlit/1.0 (reservierungs-demo)"


class DbApiError(RuntimeError):
    """Fallo al hablar con la API de la Deutsche Bahn."""


@dataclass(frozen=True)
class Station:
    """Una estación de la DB (identificada por su número EVA)."""

    eva: str
    name: str
    ds100: str = ""


@dataclass(frozen=True)
class StopEvent:
    """Un evento de parada (llegada y/o salida) de un tren en una estación."""

    id: str
    eva: str
    category: str
    number: str
    arrival_planned: datetime | None = None
    arrival_actual: datetime | None = None
    departure_planned: datetime | None = None
    departure_actual: datetime | None = None
    platform: str | None = None
    cancelled: bool = False
    # RIS: en la llegada `ppth` es la ruta hasta la estación (origen primero);
    # en la salida es la ruta desde la estación (destino último).
    path: tuple[str, ...] = ()
    arrival_path: tuple[str, ...] = ()
    line: str = ""

    @property
    def train(self) -> str:
        return f"{self.category} {self.number}".strip()

    @property
    def departure(self) -> datetime | None:
        return (
            self.departure_actual
            or self.departure_planned
            or self.arrival_actual
            or self.arrival_planned
        )

    @property
    def arrival(self) -> datetime | None:
        return self.arrival_actual or self.arrival_planned

    @property
    def delay_minutes(self) -> int:
        planned = self.departure_planned or self.arrival_planned
        actual = self.departure_actual or self.arrival_actual
        if planned is None or actual is None:
            return 0
        return int((actual - planned).total_seconds() // 60)

    @property
    def destination(self) -> str:
        """Última parada de la ruta (el destino del tren)."""
        return self.path[-1] if self.path else ""

    @property
    def origin(self) -> str:
        """Primera parada de la ruta (el origen del tren)."""
        return self.route_origin

    @property
    def route_origin(self) -> str:
        """Origen del tren según la ruta (igual en llegada y salida)."""
        route = self.arrival_path or self.path
        return route[0] if route else ""


# --------------------------------------------------------------------------- #
# Credenciales
# --------------------------------------------------------------------------- #

def _secret(name: str) -> str:
    """Lee un secreto de Streamlit (o None si la app corre sin secrets)."""
    try:
        import streamlit as st

        secrets = st.secrets
    except Exception:
        return ""
    for path in ((name,), ("db", name)):
        try:
            value = secrets
            for part in path:
                value = value[part]
            text = str(value).strip()
        except Exception:
            continue
        if text:
            return text
    return ""


def credentials() -> tuple[str, str] | None:
    """(client_id, api_key) si hay credenciales de la DB, si no None."""
    client_id = os.environ.get("DB_CLIENT_ID", "").strip()
    api_key = os.environ.get("DB_API_KEY", "").strip()
    if client_id and api_key:
        return client_id, api_key
    client_id = _secret("DB_CLIENT_ID")
    api_key = _secret("DB_API_KEY")
    if client_id and api_key:
        return client_id, api_key
    return None


def available() -> bool:
    """¿Hay credenciales configuradas para la DB?"""
    return credentials() is not None


# --------------------------------------------------------------------------- #
# Transporte HTTP
# --------------------------------------------------------------------------- #

def _headers(client_id: str, api_key: str) -> dict[str, str]:
    return {
        "DB-Client-Id": client_id,
        "DB-Api-Key": api_key,
        "Accept": "application/xml",
        "User-Agent": USER_AGENT,
    }


def _get(path: str, *, client_id: str, api_key: str, timeout: float = DB_API_TIMEOUT) -> bytes:
    url = f"{DB_API_BASE_URL}/{path.lstrip('/')}"
    request = urllib.request.Request(url, headers=_headers(client_id, api_key))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise DbApiError(
                "Die DB-API lehnt die Anmeldedaten ab (401). "
                "Prüfe DB_CLIENT_ID und DB_API_KEY in den Secrets."
            ) from error
        if error.code == 403:
            raise DbApiError("Für die DB-API fehlt die Freischaltung (403).") from error
        if error.code == 404:
            raise DbApiError(f"Keine Daten für {path} (404).") from error
        if error.code == 429:
            raise DbApiError("Zu viele Anfragen an die DB-API (429). Bitte kurz warten.") from error
        raise DbApiError(f"DB-API antwortet mit HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise DbApiError(f"DB-API nicht erreichbar: {error.reason}") from error
    except TimeoutError as error:
        raise DbApiError("Zeitüberschreitung bei der DB-API.") from error


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _parse_ris_time(value: str | None) -> datetime | None:
    """'YYMMDDHHMM' (hora local de DB) -> datetime con zona horaria."""
    if not value or len(value) != 10:
        return None
    try:
        naive = datetime.strptime(value, "%y%m%d%H%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=LOCAL_TZ)


def _parse_path(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _parse_event(node: ElementTree.Element | None) -> dict:
    if node is None:
        return {}
    return {
        "planned": _parse_ris_time(node.get("pt")),
        "actual": _parse_ris_time(node.get("ct")),
        "platform": (node.get("cp") or node.get("pp") or "").strip() or None,
        "path": _parse_path(node.get("cpth") or node.get("ppth")),
        "status": (node.get("cs") or node.get("ps") or "").strip().lower(),
        "line": (node.get("l") or "").strip(),
    }


def _root(payload: bytes | str) -> ElementTree.Element:
    """Parsea el XML de la API (con mensaje claro si la respuesta no es XML)."""
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise DbApiError("Unerwartete Antwort der DB-API (kein XML).") from error


def parse_timetable(payload: bytes | str) -> list[StopEvent]:
    """Parsea la respuesta de `/plan` o `/fchg` en eventos de parada."""
    root = _root(payload)
    events: list[StopEvent] = []
    for node in root.findall("s"):
        train = node.find("tl")
        arrival = _parse_event(node.find("ar"))
        departure = _parse_event(node.find("dp"))
        if not arrival and not departure:
            continue
        status = departure.get("status") or arrival.get("status") or ""
        path = departure.get("path") or arrival.get("path") or ()
        arrival_path = arrival.get("path") or path
        platform = departure.get("platform") or arrival.get("platform")
        line = departure.get("line") or arrival.get("line") or ""
        events.append(
            StopEvent(
                id=node.get("id") or "",
                eva=node.get("eva") or "",
                category=(train.get("c") or "").strip() if train is not None else "",
                number=(train.get("n") or "").strip() if train is not None else "",
                arrival_planned=arrival.get("planned"),
                arrival_actual=arrival.get("actual"),
                departure_planned=departure.get("planned"),
                departure_actual=departure.get("actual"),
                platform=platform,
                cancelled=status == "c",
                path=path,
                arrival_path=arrival_path,
                line=line,
            )
        )
    return events


def parse_stations(payload: bytes | str) -> list[Station]:
    """Parsea la respuesta de `/station/{pattern}` (elementos `<s>` en RIS)."""
    root = _root(payload)
    nodes = root.findall("s") or root.findall("station")
    stations: list[Station] = []
    for node in nodes:
        eva = (node.get("eva") or "").strip()
        name = (node.get("name") or "").strip()
        if not eva or not name:
            continue
        stations.append(Station(eva=eva, name=name, ds100=(node.get("ds100") or "").strip()))
    return stations


def merge_changes(event: StopEvent, change: StopEvent | None) -> StopEvent:
    """Superpone los cambios en vivo (`/fchg`) sobre el plan (`/plan`)."""
    if change is None:
        return event
    return replace(
        event,
        arrival_actual=change.arrival_actual or event.arrival_actual,
        departure_actual=change.departure_actual or event.departure_actual,
        platform=change.platform or event.platform,
        cancelled=change.cancelled or event.cancelled,
        path=change.path or event.path,
        category=change.category or event.category,
        number=change.number or event.number,
    )


def matches(event: StopEvent, category: str, number: str, origin: str = "") -> bool:
    """¿Es este evento el mismo tren que buscamos (mismo tipo, número y origen)?

    El camino completo puede variar ligeramente entre estaciones (altas y
    bajas de paradas), así que se compara el origen del tren: en RIS la ruta
    de llegada empieza en el origen y la de salida también.
    """
    if category and event.category and event.category != category:
        return False
    if number and event.number and event.number != number:
        return False
    return not (origin and event.route_origin and event.route_origin != origin)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def find_stations(pattern: str, *, client_id: str, api_key: str) -> list[Station]:
    """Busca estaciones por nombre (prefijo, como en DB Navigator)."""
    path = urllib.parse.quote(pattern.strip(), safe="")
    payload = _get(f"station/{path}", client_id=client_id, api_key=api_key)
    return parse_stations(payload)


def plan(
    eva: str,
    day: date,
    hour: int,
    *,
    client_id: str,
    api_key: str,
) -> list[StopEvent]:
    """Horario planificado de una hora en una estación.

    La API acepta la fecha en `YYMMDD`; si el despliegue usa `YYYYMMDD` se
    reintenta con ese formato.
    """
    first_error: DbApiError | None = None
    for pattern in ("%y%m%d", "%Y%m%d"):
        try:
            payload = _get(
                f"plan/{eva}/{day.strftime(pattern)}/{hour:02d}",
                client_id=client_id,
                api_key=api_key,
            )
        except DbApiError as error:
            first_error = first_error or error
            continue
        return parse_timetable(payload)
    raise first_error or DbApiError("Kein Fahrplan verfügbar.")


def changes(eva: str, *, client_id: str, api_key: str) -> dict[str, StopEvent]:
    """Cambios en vivo del día (retrasos, vías, cancelaciones) por id de parada."""
    payload = _get(f"fchg/{eva}", client_id=client_id, api_key=api_key)
    return {event.id: event for event in parse_timetable(payload)}


def board(
    eva: str,
    day: date,
    hours: range,
    *,
    client_id: str,
    api_key: str,
) -> list[StopEvent]:
    """Tablero de salidas de una estación: plan de las horas + cambios en vivo."""
    planned: list[StopEvent] = []
    for hour in hours:
        try:
            planned.extend(plan(eva, day, hour, client_id=client_id, api_key=api_key))
        except DbApiError:
            continue

    try:
        live = changes(eva, client_id=client_id, api_key=api_key)
    except DbApiError:
        live = {}

    merged: list[StopEvent] = []
    seen: set[str] = set()
    for event in planned:
        merged.append(merge_changes(event, live.get(event.id)))
        seen.add(event.id)
    for event_id, event in live.items():
        if event_id not in seen and event.departure is not None:
            merged.append(event)

    merged.sort(key=lambda item: item.departure or datetime.min.replace(tzinfo=LOCAL_TZ))
    return merged
