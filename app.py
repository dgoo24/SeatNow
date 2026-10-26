from __future__ import annotations

import datetime as dt
import html
import os
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests
import streamlit as st

import db_backend
import demo_backend

DEFAULT_API_BASE = os.environ.get("API_BASE_URL", "http://localhost:3000").rstrip("/")
DEMO_BASE = "demo://seatnow"
DB_BASE = "dbapi://seatnow"
DB_HORIZON_DAYS = 1
BERLIN = ZoneInfo("Europe/Berlin")
DIALOG_TIMEOUT = (5, 90)

NAV_SEARCH = "Verbindung suchen"
NAV_TRIP = "Wagenansicht"
NAV_BOOKINGS = "Meine Buchungen"
NAV_ADMIN = "Admin"
PAGES = {
    "Suche": NAV_SEARCH,
    "Fahrt": NAV_TRIP,
    "Buchungen": NAV_BOOKINGS,
    "Admin": NAV_ADMIN,
}

FLASH_ICONS = {
    "ok": ":material/check_circle:",
    "warn": ":material/warning:",
    "err": ":material/error:",
    "info": ":material/info:",
}
STATUS_COLORS = {
    "CONFIRMED": "green",
    "PENDING_PAYMENT": "orange",
    "CHECKED_IN": "blue",
    "NO_SHOW": "red",
    "CANCELLED": "gray",
    "FAILED": "red",
}
SEAT_STYLES = {
    "AVAILABLE": ("#123d2b", "#2fbf71", "#a7f3d0"),
    "HELD": ("#3d2f10", "#f59e0b", "#fde68a"),
    "BOOKED": ("#3d1517", "#ef4444", "#fecaca"),
}
STATUS_LABELS = {
    "CONFIRMED": "Bestätigt",
    "PENDING_PAYMENT": "Zahlung offen",
    "CHECKED_IN": "Eingecheckt",
    "NO_SHOW": "Nicht erschienen",
    "CANCELLED": "Storniert",
    "FAILED": "Fehlgeschlagen",
}
PAGE_HINTS = {
    "Suche": "Strecke wählen, Verbindungen vergleichen und Sitzplatz sichern.",
    "Fahrt": "Teilstrecke prüfen, Wagenplan lesen und Buchung abschließen.",
    "Buchungen": "Tickets, Status und Check-in an einem Ort.",
    "Admin": "Offene Reservierungen und alle Buchungen einsehen.",
}


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def api_origin() -> str:
    return (st.session_state.get("api_base") or DEFAULT_API_BASE).rstrip("/")


def api_base() -> str:
    if st.session_state.get("demo_mode"):
        return DEMO_BASE
    if st.session_state.get("db_mode"):
        return DB_BASE
    return api_origin()


def request_json(
    method: str,
    path: str,
    *,
    base: str,
    params: dict[str, Any] | None = None,
    timeout: Any = DIALOG_TIMEOUT,
) -> Any:
    if base in (DEMO_BASE, DB_BASE):
        module = db_backend if base == DB_BASE else demo_backend
        try:
            return module.handle(method, path, params=params)
        except demo_backend.DemoError as exc:
            raise ApiError(str(exc), exc.status, exc.code) from exc
    response = requests.request(method, f"{base}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def ensure_backend() -> None:
    if (
        st.session_state.get("demo_mode")
        or st.session_state.get("db_mode")
        or st.session_state.get("backend_checked")
    ):
        return
    st.session_state.backend_checked = True
    timeout = (5, 75) if st.session_state.get("api_configured") else (3, 6)
    try:
        response = requests.get(f"{api_origin()}/health", timeout=timeout)
        response.raise_for_status()
    except (requests.RequestException, ValueError):
        st.session_state.demo_mode = True


def retry_backend() -> None:
    st.session_state.demo_mode = False
    st.session_state.backend_checked = False
    fetch_health.clear()
    st.rerun()


def api(
    path: str,
    method: str = "GET",
    *,
    auth: bool = False,
    base: str | None = None,
    **kwargs: Any,
) -> Any:
    base = (base or api_base()).rstrip("/")
    token = st.session_state.get("token") if auth else None
    if base in (DEMO_BASE, DB_BASE):
        module = db_backend if base == DB_BASE else demo_backend
        try:
            return module.handle(
                method,
                path,
                params=kwargs.get("params"),
                json_body=kwargs.get("json"),
                token=token,
            )
        except demo_backend.DemoError as exc:
            raise ApiError(str(exc), exc.status, exc.code) from exc

    headers = dict(kwargs.pop("headers", {}))
    if auth and token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(
            method,
            f"{base}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", DIALOG_TIMEOUT),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise ApiError(f"Backend nicht erreichbar ({base}): {exc}") from exc

    payload: Any = None
    if response.content:
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text[:300]}

    detail = payload.get("message") if isinstance(payload, dict) else None
    code = payload.get("error") if isinstance(payload, dict) else None
    if response.status_code == 401:
        raise ApiError(detail or "Bitte melde dich an.", 401, code or "UNAUTHORIZED")
    if not response.ok:
        raise ApiError(detail or f"HTTP {response.status_code}", response.status_code, code)
    return payload


@st.cache_data(ttl=20, show_spinner=False)
def fetch_health(base: str) -> dict[str, Any]:
    return request_json("GET", "/health", base=base, timeout=6)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_stats(base: str) -> dict[str, Any]:
    return request_json("GET", "/api/trains/stats", base=base, timeout=20)


@st.cache_data(ttl=600, show_spinner=False)
def stop_suggestions(base: str, query: str, limit: int = 8) -> list[str]:
    if len(query.strip()) < 2:
        return []
    try:
        payload = request_json(
            "GET",
            "/api/trains/stops",
            base=base,
            params={"q": query.strip(), "limit": limit},
            timeout=15,
        )
        return [item["stopName"] for item in payload if item.get("stopName")]
    except (requests.RequestException, ApiError, ValueError, KeyError, TypeError):
        return []


@st.cache_data(ttl=120, show_spinner=False)
def fetch_search(base: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    payload = request_json("GET", "/api/trains/search", base=base, params=params)
    return payload if isinstance(payload, list) else []


@st.cache_data(ttl=900, show_spinner=False)
def fetch_trip(base: str, trip_id: str, date_value: str | None = None) -> dict[str, Any]:
    params = {"date": date_value} if date_value else None
    return request_json("GET", f"/api/trains/{trip_id}", base=base, params=params)


@st.cache_data(ttl=5, show_spinner=False)
def fetch_availability(base: str, trip_id: str, segment_id: str) -> list[dict[str, Any]]:
    payload = request_json(
        "GET",
        f"/api/trains/{trip_id}/segments/{segment_id}/availability",
        base=base,
        timeout=(5, 20),
    )
    return payload if isinstance(payload, list) else []


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_nearby(base: str, lat: float, lon: float) -> dict[str, Any] | None:
    try:
        return request_json(
            "GET",
            "/api/trains/stops/nearby",
            base=base,
            params={"lat": lat, "lon": lon},
            timeout=(5, 15),
        )
    except (requests.RequestException, ApiError, ValueError):
        return None


_GPS_CSS = """
:host,
.gps {
  display: none;
}
"""

_GPS_HTML = """
<div class="gps">
  <span id="gps-status" class="gps-status">Standort wird ermittelt …</span>
</div>
"""

_GPS_JS = """
export default function (component) {
  const { parentElement, data, setTriggerValue } = component
  const root = parentElement.querySelector(".gps")
  if (!root) return
  const status = root.querySelector("#gps-status")
  if (!status) return

  const setStatus = (text, tone) => {
    status.textContent = text
    status.dataset.tone = tone || "muted"
  }

  if (data && data.station) setStatus("GPS: " + data.station, "ok")

  const ask = () => {
    if (!navigator.geolocation) {
      setStatus("GPS im Browser nicht verfügbar", "error")
      return
    }
    setStatus("Standort wird ermittelt …", "muted")
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setTriggerValue("located", {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
        })
      },
      (err) => {
        setStatus(
          err.code === 1 ? "Standortfreigabe nötig" : "Standort nicht verfügbar",
          "error",
        )
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
    )
  }

  if (!root.dataset.auto) {
    root.dataset.auto = "1"
    ask()
  }
}
"""

_gps_widget = st.components.v2.component(
    "gps_origin_picker",
    html=_GPS_HTML,
    css=_GPS_CSS,
    js=_GPS_JS,
)


def gps_origin(base: str) -> None:
    station = st.session_state.get("gps_stop")
    data = {"station": station["stopName"]} if station else {}
    result = _gps_widget(
        key="gps_origin",
        data=data,
        on_located_change=lambda: None,
    )
    located = getattr(result, "located", None)
    if not located:
        return
    try:
        lat = float(located["lat"])
        lon = float(located["lon"])
    except (KeyError, TypeError, ValueError):
        return
    signature = (round(lat, 4), round(lon, 4))
    if st.session_state.get("gps_signature") == signature:
        return
    st.session_state.gps_signature = signature
    stop = fetch_nearby(base, lat, lon)
    if not stop:
        return
    st.session_state.gps_stop = stop
    current = st.session_state.get("f_origin") or ""
    if current and current != st.session_state.get("gps_origin_name"):
        return
    st.session_state.f_origin = stop["stopName"]
    st.session_state.gps_origin_name = stop["stopName"]
    st.rerun()


def now_time() -> dt.time:
    current = dt.datetime.now(BERLIN)
    floored = current.replace(minute=current.minute - current.minute % 5, second=0, microsecond=0)
    return floored.time()


def fmt_clock(value: Any) -> str:
    if not value:
        return "--:--"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[11:16] or "--:--"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(BERLIN).strftime("%H:%M")


def fmt_datetime(value: Any) -> str:
    if not value:
        return "–"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")


def hhmm(value: Any, mark_next_day: bool = False) -> str:
    if not value:
        return "--:--"
    text = str(value)
    if "T" in text:
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(BERLIN).strftime("%H:%M")
    parts = text.split(":")
    try:
        hours = int(parts[0])
    except ValueError:
        return str(value)
    minutes = parts[1] if len(parts) > 1 and str(parts[1]).isdigit() else "00"
    text = f"{hours % 24:02d}:{int(minutes):02d}"
    return f"{text} +1" if mark_next_day and hours >= 24 else text


def fmt_duration(minutes: Any) -> str:
    if minutes is None:
        return "–"
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "–"
    return f"{total // 60}:{total % 60:02d} h"


def fmt_money(cents: Any) -> str:
    if cents is None:
        return "–"
    try:
        value = float(cents) / 100
    except (TypeError, ValueError):
        return "–"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "–"


def status_label(booking: dict[str, Any]) -> str:
    if booking.get("isCheckedIn"):
        return STATUS_LABELS["CHECKED_IN"]
    return STATUS_LABELS.get(str(booking.get("status")), str(booking.get("status") or "–"))


def train_label(item: dict[str, Any]) -> str:
    name = str(item.get("routeName") or "")
    number = item.get("trainNumberForDate") or item.get("trainNumber")
    if number is not None and str(number).strip().isdigit():
        prefix = re.match(r"^([A-Za-z]{2,4})", name)
        return f"{(prefix.group(1).upper() if prefix else 'ICE')} {number}"
    if number:
        return f"Tf {number}"
    match = re.match(r"^([A-Za-z]{2,4})\s+(\d{1,5})", name)
    if match:
        return f"{match.group(1).upper()} {match.group(2)}"
    return name or "Zug"


def segment_label(segment: dict[str, Any]) -> str:
    return (
        f"{segment.get('from')} → {segment.get('to')} · "
        f"{hhmm(segment.get('departureTime'))}–{hhmm(segment.get('arrivalTime'), True)} · "
        f"{fmt_money(segment.get('price'))} ({segment.get('durationMinutes')} Min.)"
    )


def seconds_left(value: Any) -> int:
    if not value:
        return 0
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return max(0, int((parsed - dt.datetime.now(dt.timezone.utc)).total_seconds()))


def _secret_value(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    text = str(value).strip() if value else ""
    if not text:
        text = (os.environ.get(name) or "").strip()
    return text or None


def _secret_api_base() -> str | None:
    try:
        value = st.secrets.get("API_BASE_URL")
    except Exception:
        return None
    return str(value).rstrip("/") if value else None


def init_state() -> None:
    secret_base = _secret_api_base()
    env_base = (os.environ.get("API_BASE_URL") or "").strip() or None
    configured_base = secret_base or env_base
    db_client_id = _secret_value("DB_CLIENT_ID")
    db_api_key = _secret_value("DB_API_KEY")
    db_ready = bool(db_client_id and db_api_key) and not configured_base
    if db_ready:
        db_backend.configure(db_client_id, db_api_key, _secret_value("DB_MARKETPLACE_URL"))
    defaults: dict[str, Any] = {
        "api_base": configured_base or DEFAULT_API_BASE,
        "api_configured": bool(configured_base),
        "db_mode": db_ready and os.environ.get("SEATNOW_DEMO") != "1",
        "demo_mode": os.environ.get("SEATNOW_DEMO") == "1",
        "backend_checked": False,
        "page": "Suche",
        "results": [],
        "search_params": None,
        "searched": False,
        "trip_ctx": None,
        "hold": None,
        "ticket": None,
        "flash": None,
        "token": None,
        "user": None,
        "validated_token": None,
        "f_date": dt.date.today(),
        "f_time_from": now_time(),
        "f_time_to": None,
        "f_time_type": "Abfahrt",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if st.session_state.get("db_mode"):
        today = dt.datetime.now(BERLIN).date()
        current = st.session_state.get("f_date")
        if (
            not isinstance(current, dt.date)
            or current < today
            or current > today + dt.timedelta(days=DB_HORIZON_DAYS)
        ):
            st.session_state.f_date = today


def clear_auth() -> None:
    st.session_state.token = None
    st.session_state.user = None
    st.session_state.validated_token = None


def flash(kind: str, message: str) -> None:
    st.session_state.flash = (kind, message)


def report_error(exc: Exception) -> None:
    if isinstance(exc, ApiError) and exc.status == 401:
        clear_auth()
        flash("warn", "Deine Sitzung ist abgelaufen. Bitte erneut anmelden.")
        st.rerun()
    st.error(str(exc))


def render_flash() -> None:
    item = st.session_state.pop("flash", None)
    if not item:
        return
    kind, message = item
    st.toast(message, icon=FLASH_ICONS.get(kind, FLASH_ICONS["info"]))


def logout() -> None:
    clear_auth()
    st.session_state.hold = None
    st.session_state.ticket = None
    flash("info", "Abgemeldet.")


def validate_session() -> None:
    token = st.session_state.get("token")
    if not token or st.session_state.get("validated_token") == token:
        return
    try:
        st.session_state.user = api("/api/auth/me", auth=True, timeout=(5, 15))
        st.session_state.validated_token = token
    except ApiError as exc:
        if exc.status == 401:
            clear_auth()
            flash("warn", "Deine Sitzung ist abgelaufen. Bitte erneut anmelden.")
    except Exception:
        pass


def do_auth(path: str, email: str, password: str) -> None:
    body: dict[str, Any] = {"email": email.strip(), "password": password}
    if path.endswith("register"):
        body["fullName"] = email.split("@")[0] or "Reisende:r"
    try:
        result = api(path, "POST", json=body)
    except ApiError as exc:
        st.error(str(exc))
        return
    st.session_state.token = result["token"]
    st.session_state.user = result["user"]
    st.session_state.validated_token = result["token"]
    st.session_state.hold = None
    st.session_state.ticket = None
    flash("ok", f"Angemeldet als {result['user'].get('fullName') or email}.")
    st.rerun()


def apply_suggestion(pill_key: str, target_key: str) -> None:
    picked = st.session_state.get(pill_key)
    if picked:
        st.session_state[target_key] = picked


def swap_stops() -> None:
    origin = st.session_state.get("f_origin")
    destination = st.session_state.get("f_destination")
    st.session_state.f_origin = destination or ""
    st.session_state.f_destination = origin or ""


def reset_search() -> None:
    st.session_state.f_origin = (st.session_state.get("gps_stop") or {}).get("stopName") or ""
    st.session_state.f_destination = ""
    st.session_state.f_time_from = now_time()
    st.session_state.f_time_to = None
    st.session_state.search_params = None
    st.session_state.results = []
    st.session_state.searched = False
    st.session_state.search_notice = None
    st.session_state.trip_ctx = None
    st.session_state.hold = None
    st.session_state.ticket = None
    st.session_state.f_date = dt.date.today()
    st.session_state.f_time_type = "Abfahrt"


def load_example(origin: str, destination: str) -> None:
    st.session_state.f_origin = origin
    st.session_state.f_destination = destination
    st.session_state.search_params = None
    st.session_state.results = []
    st.session_state.searched = False
    st.session_state.auto_search = True


def stop_input(
    label: str,
    key: str,
    placeholder: str,
    base: str,
    help: str | None = None,
) -> str:
    value = st.text_input(label, key=key, placeholder=placeholder, help=help)
    query = (value or "").strip()
    suggestions = stop_suggestions(base, query, limit=50)
    if suggestions and query not in suggestions:
        shown = suggestions[:12]
        pill_key = f"{key}::suggest::{query.lower()}"
        st.pills(
            "Vorschläge",
            shown,
            key=pill_key,
            on_change=apply_suggestion,
            args=(pill_key, key),
            label_visibility="collapsed",
        )
        if len(suggestions) > len(shown):
            with st.expander(
                f"Weitere passende Bahnhöfe ({len(suggestions) - len(shown)})"
            ):
                for name in suggestions[len(shown):]:
                    if st.button(
                        name,
                        key=f"{key}::more::{name}",
                        width="stretch",
                    ):
                        st.session_state[key] = name
                        st.rerun()
    return query


def open_trip(row: dict[str, Any]) -> None:
    params = st.session_state.get("search_params") or {}
    st.session_state.trip_ctx = {
        "id": row["id"],
        "date": params.get("date"),
        "segment_id": row.get("segmentId"),
    }
    if row.get("segmentId"):
        st.session_state[f"segment_{row['id']}"] = row["segmentId"]
    st.session_state.hold = None
    st.session_state.ticket = None
    st.session_state.page = "Fahrt"


def refresh_search() -> None:
    params = st.session_state.get("search_params")
    if not params:
        return
    try:
        fetch_search.clear()
        rows = fetch_search(api_base(), params)
    except (requests.RequestException, ApiError) as exc:
        flash("err", f"Backend nicht erreichbar ({api_base()}): {exc}")
        return
    st.session_state.results = rows
    flash("ok", "Ergebnisse aktualisiert.")


def go_search() -> None:
    st.session_state.page = "Suche"


def go_bookings() -> None:
    st.session_state.page = "Buchungen"


def do_search(
    origin: str,
    destination: str,
    date_value: dt.date,
    time_mode: str,
    time_from: dt.time | None,
    time_to: dt.time | None,
) -> None:
    origin = (origin or "").strip()
    destination = (destination or "").strip()
    if not origin and not destination:
        st.session_state.results = []
        st.session_state.search_params = {"date": date_value.isoformat()}
        st.session_state.search_notice = None
        st.session_state.searched = True
        st.session_state.hold = None
        st.session_state.ticket = None
        flash("warn", "Bitte wähle Start und Ziel – oder nutze eine Beispielstrecke.")
        return

    params: dict[str, Any] = {
        "date": date_value.isoformat(),
        "limit": 20,
        "timeType": "arrival" if time_mode == "Ankunft" else "departure",
    }
    if origin:
        params["origin"] = origin
    if destination:
        params["destination"] = destination
    if time_from:
        params["timeFrom"] = time_from.strftime("%H:%M")
    if time_to:
        params["timeTo"] = time_to.strftime("%H:%M")
    with st.spinner("Suche läuft …"):
        try:
            rows = fetch_search(api_base(), params)
        except (requests.RequestException, ApiError) as exc:
            if isinstance(exc, ApiError) and exc.code == "NO_DATA":
                st.session_state.results = []
                st.session_state.search_params = params
                st.session_state.search_notice = str(exc)
                st.session_state.searched = True
                st.session_state.hold = None
                st.session_state.ticket = None
            else:
                report_error(ApiError(f"Backend nicht erreichbar ({api_base()}): {exc}"))
            return
    st.session_state.results = rows
    st.session_state.search_params = params
    st.session_state.search_notice = None
    st.session_state.searched = True
    st.session_state.hold = None
    st.session_state.ticket = None


def seat_map_html(availability: list[dict[str, Any]]) -> str:
    by_car: dict[int, list[dict[str, Any]]] = {}
    for seat in availability:
        try:
            car = int(seat.get("carNumber") or 0)
        except (TypeError, ValueError):
            car = 0
        by_car.setdefault(car, []).append(seat)

    blocks: list[str] = []
    for car in sorted(by_car):
        cells: list[str] = []
        seats = sorted(by_car[car], key=lambda item: str(item.get("seatNumber") or ""))
        for seat in seats:
            status = str(seat.get("status") or "AVAILABLE")
            background, border, color = SEAT_STYLES.get(status, SEAT_STYLES["AVAILABLE"])
            reservations = seat.get("reservations") or []
            tooltip = "Frei in dieser Teilstrecke"
            if reservations:
                tooltip = "Belegt: " + "; ".join(
                    f"{item.get('from')}→{item.get('to')} ({str(item.get('status', '')).lower()})"
                    for item in reservations
                )
            cells.append(
                '<span title="{title}" style="display:flex;align-items:center;justify-content:center;'
                "height:30px;border-radius:6px;border:1px solid {border};background:{background};"
                'color:{color};font-size:12px;font-weight:600">{label}</span>'.format(
                    title=html.escape(tooltip, quote=True),
                    border=border,
                    background=background,
                    color=color,
                    label=html.escape(str(seat.get("seatNumber") or "")),
                )
            )
        blocks.append(
            '<div style="flex:1 1 190px;min-width:190px;padding:10px 12px;border-radius:10px;'
            'border:1px solid #262e47;background:#101527">'
            '<div style="font-size:12px;font-weight:700;letter-spacing:.04em;'
            'text-transform:uppercase;color:#8fa0c4;margin-bottom:8px">Wagen {car}</div>'
            '<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px">{cells}</div>'
            "</div>".format(car=car, cells="".join(cells))
        )

    legend = (
        '<div style="display:flex;gap:14px;flex-wrap:wrap;margin:2px 0 8px;font-size:12px;color:#aab4cc">'
        '<span><span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
        'background:#2fbf71;margin-right:5px"></span>frei</span>'
        '<span><span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
        'background:#f59e0b;margin-right:5px"></span>reserviert</span>'
        '<span><span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
        'background:#ef4444;margin-right:5px"></span>gebucht</span>'
        "</div>"
    )
    if not blocks:
        blocks.append('<div style="color:#aab4cc">Keine Sitzplätze gefunden.</div>')
    return legend + '<div style="display:flex;flex-wrap:wrap;gap:12px">' + "".join(blocks) + "</div>"


def stops_html(details: dict[str, Any]) -> str:
    focus = {details.get("originStop"), details.get("destinationStop")}
    chips: list[str] = []
    for stop in details.get("stops") or []:
        name = str(stop.get("stopName") or "")
        active = name in focus
        background = "#2a0f14" if active else "#141a2b"
        border = "#ec0016" if active else "#262e47"
        color = "#ffb3bd" if active else "#aab4cc"
        chips.append(
            '<span style="display:inline-flex;align-items:center;gap:6px;padding:5px 9px;'
            "border-radius:999px;border:1px solid {border};background:{background};color:{color};"
            'font-size:12px">{name} <b style="color:#eef1f8">{time}</b></span>'.format(
                border=border,
                background=background,
                color=color,
                name=html.escape(name),
                time=html.escape(hhmm(stop.get("departureTime") or stop.get("arrivalTime"), True)),
            )
        )
    return '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 14px">' + "".join(chips) + "</div>"


@st.fragment(run_every="1s")
def hold_countdown(expires_at: str) -> None:
    remaining = seconds_left(expires_at)
    if remaining <= 0:
        st.session_state.hold = None
        flash("warn", "Die Reservierung ist abgelaufen. Der Sitzplatz wurde freigegeben.")
        st.rerun(scope="app")
        return
    minutes, seconds = divmod(remaining, 60)
    color = "#f59e0b" if remaining <= 60 else "#eef1f8"
    st.html(
        f'<div style="font-size:13px;color:#aab4cc">Verbleibende Zeit bis zur '
        f'Freigabe: <b style="color:{color};font-size:16px">{minutes}:{seconds:02d}</b> Min.</div>'
    )


def checkout_hold(hold: dict[str, Any]) -> None:
    try:
        result = api(
            "/api/bookings/checkout",
            "POST",
            auth=True,
            json={"bookingId": hold["booking"]["id"]},
        )
    except ApiError as exc:
        report_error(exc)
        return
    st.session_state.hold = None
    st.session_state.ticket = result
    fetch_availability.clear()
    flash("ok", "Zahlung erfolgreich. Sitzplatz bestätigt.")
    st.rerun()


def cancel_booking(booking_id: str, *, from_hold: bool = False) -> None:
    try:
        api(f"/api/bookings/{booking_id}/cancel", "POST", auth=True, json={})
    except ApiError as exc:
        report_error(exc)
        return
    if from_hold:
        st.session_state.hold = None
    fetch_availability.clear()
    flash("ok", "Buchung storniert, Sitzplatz freigegeben.")
    st.rerun()


def check_in_booking(booking_id: str, qr_payload: str | None) -> None:
    body: dict[str, Any] = {"qrPayload": qr_payload} if qr_payload else {}
    try:
        api(f"/api/bookings/{booking_id}/check-in", "POST", auth=True, json=body)
    except ApiError as exc:
        report_error(exc)
        return
    flash("ok", "Check-in bestätigt. Gute Reise!")
    st.rerun()


@st.fragment(run_every="60s")
def realtime_panel(trip_id: str, date_value: str | None) -> None:
    key = f"rt_cache_{trip_id}_{date_value or ''}"
    cached = st.session_state.get(key)
    now = time.monotonic()
    if cached and now - cached.get("ts", 0) < 55:
        _render_realtime(cached["data"])
        return
    params = {"date": date_value} if date_value else None
    try:
        data = api(f"/api/trains/{trip_id}/realtime", params=params, timeout=(5, 20))
    except ApiError as exc:
        data = {"available": False, "message": str(exc), "fetchedAt": None}
    st.session_state[key] = {"data": data, "ts": now}
    _render_realtime(data)


def _render_realtime(data: dict[str, Any]) -> None:
    if not data.get("available"):
        st.warning(f"Echtzeit DB: {data.get('message') or 'nicht verfügbar'}")
        st.caption(f"Aktualisiert {fmt_clock(data.get('fetchedAt'))}")
        return

    live = data.get("live") or {}
    with st.container(border=True):
        st.markdown("**Echtzeit DB**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if live.get("cancelled"):
                st.badge("Ausgefallen", color="red", icon=":material/block:")
            elif live.get("delayMinutes"):
                st.badge(
                    f"Verspätung {live['delayMinutes']} Min.",
                    color="orange",
                    icon=":material/schedule:",
                )
            else:
                st.badge("Pünktlich", color="green", icon=":material/check_circle:")
        with c2:
            platform = live.get("platform")
            if platform:
                planned = live.get("plannedPlatform")
                caption = f"geplant {planned}" if planned and planned != platform else None
                st.metric("Gleis", platform, help=caption)
        with c3:
            if live.get("station"):
                st.metric("Station", live["station"])
        with c4:
            if live.get("line"):
                st.metric("Zug", live["line"])

        details: list[str] = []
        if live.get("actualDeparture") and live.get("actualDeparture") != live.get("plannedDeparture"):
            details.append(f"Tatsächl. Abfahrt {live['actualDeparture']}")
        if live.get("actualArrival") and live.get("actualArrival") != live.get("plannedArrival"):
            details.append(f"Tatsächl. Ankunft {live['actualArrival']}")
        if live.get("position"):
            position = live["position"]
            if position.get("kind") == "latlon":
                details.append(
                    f"Position {float(position.get('latitude', 0)):.3f}, "
                    f"{float(position.get('longitude', 0)):.3f}"
                )
            elif position.get("name"):
                details.append(f"Position {position['name']}")
        if details:
            st.caption(" · ".join(details))

        next_stops = live.get("nextStops") or []
        if next_stops:
            parts = []
            for stop in next_stops:
                when = fmt_clock(stop.get("actual") or stop.get("planned"))
                delay = f" (+{stop['delayMinutes']})" if stop.get("delayMinutes") else ""
                parts.append(f"{stop.get('name')} {when}{delay}")
            st.caption("Nächste Halte: " + " · ".join(parts))
        st.caption(f"Aktualisiert {fmt_clock(data.get('fetchedAt'))}")


def seat_picker(availability: list[dict[str, Any]], segment_id: str) -> dict[str, Any] | None:
    free = {
        str(seat.get("seatId")): seat
        for seat in availability
        if seat.get("status") == "AVAILABLE"
    }
    if not free:
        st.warning("In dieser Teilstrecke sind keine Sitzplätze mehr frei.")
        return None

    pills_key = f"seat_pills_{segment_id}"
    if st.session_state.get(pills_key) not in free:
        st.session_state.pop(pills_key, None)

    st.markdown("**2. Freien Platz antippen**")
    st.caption("Alle freien Plätze dieser Teilstrecke – antippen zum Auswählen.")
    selected_id = st.pills(
        "2. Freien Platz antippen",
        list(free),
        format_func=lambda seat_id: (
            f"Wagen {free[seat_id].get('carNumber')} · {free[seat_id].get('seatNumber')}"
        ),
        key=pills_key,
        label_visibility="collapsed",
        help="Tippe auf einen Platz, um ihn für die Reservierung auszuwählen.",
        wrap=True,
    )
    picked = free.get(selected_id)
    if picked:
        st.caption(
            f"Ausgewählt: Wagen {picked.get('carNumber')} · Platz {picked.get('seatNumber')}"
        )
    return picked


@st.fragment(run_every="30s")
def availability_panel(
    trip_id: str,
    segments: list[dict[str, Any]],
    initial_segment_id: str | None,
) -> None:
    if not segments:
        st.warning("Diese Fahrt hat keine Teilstrecken.")
        return

    options = [segment["id"] for segment in segments]
    segment_key = f"segment_{trip_id}"
    if segment_key not in st.session_state and initial_segment_id in options:
        st.session_state[segment_key] = initial_segment_id

    labels = {segment["id"]: segment_label(segment) for segment in segments}
    segment_id = st.selectbox(
        "1. Teilstrecke wählen",
        options,
        format_func=lambda value: labels.get(value, value),
        key=segment_key,
        help="Plätze gelten nur für den gewählten Abschnitt. Preis und Dauer stehen in der Auswahl.",
    )
    segment = next((item for item in segments if item["id"] == segment_id), None)
    if segment is None:
        return

    try:
        availability = fetch_availability(api_base(), trip_id, segment_id)
    except (requests.RequestException, ApiError) as exc:
        st.error(f"Backend nicht erreichbar ({api_base()}): {exc}")
        return

    free_seats = [seat for seat in availability if seat.get("status") == "AVAILABLE"]
    hold = st.session_state.get("hold")
    if not st.session_state.get("token"):
        st.html(seat_map_html(availability))
        st.info(
            "Zum Reservieren bitte oben rechts anmelden. Suchen und Vergleichen "
            "ist auch ohne Konto möglich."
        )
    elif not free_seats:
        st.html(seat_map_html(availability))
        st.warning("In dieser Teilstrecke sind keine Sitzplätze mehr frei.")
    elif hold:
        st.html(seat_map_html(availability))
        st.caption("Es gibt bereits eine aktive Reservierung. Bitte zuerst bezahlen oder stornieren.")
    else:
        picked = seat_picker(availability, segment_id)
        if st.button(
            "Sitzplatz reservieren",
            type="primary",
            icon=":material/event_seat:",
            width="stretch",
            disabled=picked is None,
            key=f"hold_btn_{segment_id}",
            help="Sperrt den Platz für wenige Minuten – bezahlt wird im nächsten Schritt.",
        ):
            try:
                result = api(
                    "/api/bookings/hold",
                    "POST",
                    auth=True,
                    json={"tripId": trip_id, "seatId": picked["seatId"], "segmentId": segment_id},
                )
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.session_state.hold = {
                    "booking": result["booking"],
                    "expiresAt": result["expiresAt"],
                    "seat": picked,
                    "segment": segment,
                    "tripId": trip_id,
                }
                st.session_state.ticket = None
                fetch_availability.clear()
                flash(
                    "ok",
                    f"Sitzplatz reserviert. Du hast {result.get('ttlSeconds', 0)} s zum Bezahlen.",
                )
                st.rerun(scope="app")

    if st.button(
        "Verfügbarkeit aktualisieren",
        icon=":material/refresh:",
        key=f"refresh_{segment_id}",
    ):
        st.rerun()


def hold_panel() -> None:
    ticket = st.session_state.get("ticket")
    if ticket:
        booking = ticket.get("booking") or {}
        seat = booking.get("seat") or {}
        st.success("Buchung bestätigt – dein Sitzplatz ist reserviert.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Sitzplatz", f"Wagen {seat.get('carNumber', '–')} · {seat.get('seatNumber', '–')}")
        c2.metric("Betrag", fmt_money(booking.get("totalAmount")))
        c3.metric("Status", status_label(booking))
        st.caption(
            f"Zahlung: {ticket.get('payment') or '–'} · PDF per E-Mail: "
            f"{'gesendet' if ticket.get('emailSent') else 'SMTP nicht konfiguriert'}"
        )
        st.markdown("**Dein Boarding-Pass (QR)**")
        st.caption(
            "Zeige diesen Code beim Einstieg. Du findest das Ticket jederzeit "
            "unter „Buchungen“."
        )
        st.code(booking.get("qrPayload") or "–", language=None)
        b1, b2 = st.columns(2)
        b1.button(
            "Zu meinen Buchungen",
            icon=":material/confirmation_number:",
            on_click=go_bookings,
            width="stretch",
            key=f"ticket_bookings_{booking.get('id')}",
        )
        if b2.button(
            "Schließen",
            icon=":material/check:",
            width="stretch",
            key=f"ticket_close_{booking.get('id')}",
        ):
            st.session_state.ticket = None
            st.rerun()
        return

    hold = st.session_state.get("hold")
    if not hold:
        return
    booking = hold["booking"]
    segment = hold["segment"]
    seat = hold["seat"]

    with st.container(border=True):
        st.markdown("#### Reservierung – Schritt 3")
        st.caption(
            "Der Platz ist jetzt für dich gesperrt. Bezahle innerhalb der Zeit, "
            "sonst wird er wieder freigegeben."
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Sitzplatz", f"Wagen {seat.get('carNumber')} · {seat.get('seatNumber')}")
        c2.metric("Preis", fmt_money(booking.get("totalAmount")))
        c3.metric("Buchung", str(booking.get("id", ""))[:8])
        distance = segment.get("distanceKm")
        distance_text = f"{float(distance):.0f} km" if distance is not None else "–"
        st.caption(
            f"{segment.get('from')} → {segment.get('to')} · "
            f"{hhmm(segment.get('departureTime'))}–{hhmm(segment.get('arrivalTime'), True)} · "
            f"{segment.get('durationMinutes')} Min. · {distance_text}"
        )
        hold_countdown(hold["expiresAt"])
        b1, b2 = st.columns(2)
        if b1.button(
            "Jetzt bezahlen",
            type="primary",
            icon=":material/payments:",
            width="stretch",
            key=f"pay_{booking.get('id')}",
            help="Simulierte Zahlung: danach bekommst du sofort dein QR-Ticket.",
        ):
            checkout_hold(hold)
        if b2.button(
            "Stornieren",
            icon=":material/close:",
            width="stretch",
            key=f"cancel_hold_{booking.get('id')}",
            help="Gibt den Platz sofort wieder frei.",
        ):
            cancel_booking(booking["id"], from_hold=True)


def connection_card(row: dict[str, Any], index: int) -> None:
    seats = row.get("availableSeats")
    if seats is None:
        seat_color, seat_label = "gray", "Plätze: –"
    elif seats == 0:
        seat_color, seat_label = "red", "Ausgebucht"
    elif seats < 10:
        seat_color, seat_label = "orange", f"Nur {seats} frei"
    else:
        seat_color, seat_label = "green", f"{seats} Plätze frei"

    with st.container(border=True):
        left, middle, right = st.columns([1.15, 3.6, 1.35], vertical_alignment="center")
        with left:
            st.badge(train_label(row), color="blue")
            st.badge(seat_label, color=seat_color, icon=":material/event_seat:")
        with middle:
            st.markdown(
                f"### {hhmm(row.get('departureTime'))} → "
                f"{hhmm(row.get('arrivalTime'), True)}"
            )
            st.caption(
                f"{row.get('originStop')} → {row.get('destinationStop')} · "
                f"{fmt_duration(row.get('durationMinutes'))} Fahrzeit"
            )
        with right:
            st.button(
                "Sitzplatz wählen",
                icon=":material/event_seat:",
                key=f"open_trip_{row.get('id')}_{index}",
                on_click=open_trip,
                args=(row,),
                width="stretch",
                help="Öffnet den Wagenplan für diese Verbindung.",
            )


def render_search_steps() -> None:
    st.markdown("#### :material/route: So funktioniert's")
    steps = [
        (
            ":material/edit_location:",
            "1. Strecke wählen",
            "Start und Ziel eingeben. Datum und Uhrzeit sind schon auf „jetzt“ "
            "gesetzt, der Startbahnhof kommt per GPS.",
        ),
        (
            ":material/compare_arrows:",
            "2. Verbindung vergleichen",
            "Abfahrt, Fahrzeit und freie Plätze vergleichen. Grün heißt: viele "
            "Plätze frei, orange: nur wenige, rot: ausgebucht.",
        ),
        (
            ":material/event_seat:",
            "3. Sitzplatz sichern",
            "Teilstrecke und Platz antippen, reservieren, bezahlen – danach "
            "bekommst du dein QR-Ticket.",
        ),
    ]
    for column, (icon, title, text) in zip(st.columns(3), steps):
        with column:
            st.markdown(f"**{icon} {title}**")
            st.caption(text)


def render_examples() -> None:
    st.caption("Schnellstart mit einer Beispielstrecke")
    if st.session_state.get("db_mode"):
        examples = [
            ("Frankfurt Hbf", "Hamburg Hbf"),
            ("München Hbf", "Berlin Hbf"),
            ("Köln Hbf", "Berlin Hbf"),
        ]
    else:
        examples = [
            ("Fulda", "Frankfurt Hbf"),
            ("Frankfurt Hbf", "Berlin Hbf"),
            ("Köln Hbf", "München Hbf"),
        ]
    with st.container(horizontal=True):
        for origin, destination in examples:
            st.button(
                f"{origin} → {destination}",
                icon=":material/route:",
                key=f"example_{origin}_{destination}",
                on_click=load_example,
                args=(origin, destination),
            )


def render_glossary() -> None:
    with st.expander("Gut zu wissen: Begriffe & Tipps", icon=":material/menu_book:"):
        st.markdown(
            "- **Teilstrecke:** ein Abschnitt der Gesamtstrecke, zum Beispiel "
            "Fulda → Frankfurt. Plätze werden für jeden Abschnitt einzeln vergeben.\n"
            "- **Wagen:** der Waggon, in dem dein Sitzplatz liegt (z. B. Wagen 3).\n"
            "- **Reservierung:** dein Platz wird für einige Minuten gesperrt, bis "
            "du bezahlst.\n"
            "- **Check-in:** bestätigt beim Einstieg, dass du tatsächlich mitfährst.\n"
            "- **Live-Daten:** aktuelle Abfahrten, Gleise und Verspätungen der "
            "Deutschen Bahn."
        )
        st.markdown("**Häufige Fragen**")
        st.markdown(
            "- **Suchen ohne Konto?** Ja. Zum Reservieren brauchst du ein Konto – "
            "die Demo-Anmeldung ist vorausgefüllt.\n"
            "- **Wann ist mein Platz garantiert?** Erst nach der Zahlung; vorher "
            "ist er nur kurz reserviert.\n"
            "- **Kann ich stornieren?** Ja, unter „Buchungen“, solange du nicht "
            "eingecheckt hast.\n"
            "- **Warum fehlt manchmal die Zugnummer?** Sie wird live nachgeladen. "
            "Mit „Ergebnisse aktualisieren“ erscheint sie meist sofort."
        )


def view_search() -> None:
    base = api_base()
    db_mode = bool(st.session_state.get("db_mode"))
    st.subheader(":material/train: Verbindung suchen")
    if db_mode:
        st.caption(
            "Echte Fahrpläne der Deutschen Bahn (DB API Marketplace) für direkte "
            "Verbindungen. Sitzplätze und Preise sind simuliert."
        )
    else:
        st.caption(
            "Sitzplätze auf Teilstrecken mit Live-Daten der Deutschen Bahn. "
            "Aliasse wie „Munich“, „Koeln“ oder „Freiburg im Breisgau“ funktionieren."
        )
    render_examples()

    with st.container(border=True):
        st.markdown("**Deine Reise**")
        c1, c2 = st.columns(2)
        with c1:
            gps_origin(base)
            origin = stop_input(
                "Von",
                "f_origin",
                "z. B. Frankfurt Hbf",
                base,
                help="Mindestens 2 Buchstaben tippen und Enter drücken – dann erscheinen passende Bahnhöfe.",
            )
        with c2:
            destination = stop_input(
                "Nach",
                "f_destination",
                "z. B. Fulda",
                base,
                help="Auch Städtenamen wie „Munich“ oder „Koeln“ werden erkannt.",
            )
        st.caption(
            ":material/keyboard: Nach dem Tippen **Enter** drücken – dann erscheinen "
            "die Bahnhofsvorschläge. Du kannst auch direkt suchen: „Hamm“ wird "
            "automatisch zu „Hamm (Westf) Hbf“. Es stehen **5.408 Bahnhöfe** zur "
            "Auswahl – auch kleine Halte (z. B. „Hamm-Heessen“)."
        )

        c3, c4 = st.columns(2)
        date_kwargs: dict[str, Any] = {}
        if db_mode:
            today = dt.datetime.now(BERLIN).date()
            date_kwargs = {
                "min_value": today,
                "max_value": today + dt.timedelta(days=DB_HORIZON_DAYS),
            }
        date_value = c3.date_input(
            "Datum",
            key="f_date",
            help="Für welchen Tag möchtest du fahren?",
            **date_kwargs,
        )
        time_mode = c4.segmented_control(
            "Zeitfilter",
            ["Abfahrt", "Ankunft"],
            key="f_time_type",
            width="stretch",
            help="Filtert Verbindungen nach Abfahrts- oder Ankunftszeit.",
        )

        c5, c6 = st.columns(2)
        time_from = c5.time_input(
            "Zeit von",
            step=300,
            key="f_time_from",
            help="Standard ist die aktuelle Uhrzeit. Frühere Züge: Uhrzeit zurücksetzen.",
        )
        time_to = c6.time_input(
            "Zeit bis (optional)",
            value=None,
            step=300,
            key="f_time_to",
            help="Leer lassen, wenn du den ganzen Tag sehen möchtest.",
        )

        if db_mode:
            st.caption(
                ":material/info: Die DB-API liefert Fahrpläne nur für heute und die "
                "nächsten ~24 Stunden und nur **direkte** Verbindungen (kein Umstieg). "
                "Sitzplätze und Preise sind simuliert."
            )

        search_clicked = st.button(
            "Verbindungen suchen",
            type="primary",
            icon=":material/search:",
            width="stretch",
            help="Sucht Teilstrecken für die gewählte Zeit.",
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button(
                "Tauschen",
                icon=":material/swap_horiz:",
                on_click=swap_stops,
                help="Tauscht Start und Ziel.",
            )
            st.button(
                "Zurücksetzen",
                icon=":material/restart_alt:",
                on_click=reset_search,
                help="Leert die Suche und setzt die Zeit auf jetzt.",
            )

    if not st.session_state.get("searched"):
        render_search_steps()

    if search_clicked:
        do_search(origin, destination, date_value, time_mode or "Abfahrt", time_from, time_to)
    elif st.session_state.pop("auto_search", False):
        do_search(origin, destination, date_value, time_mode or "Abfahrt", time_from, time_to)

    results = st.session_state.get("results") or []
    if not st.session_state.get("searched"):
        st.caption(
            "Noch keine Suche. Wähle oben eine Strecke – oder nutze ein Beispiel."
        )
        render_glossary()
        return
    if not results:
        notice = st.session_state.pop("search_notice", None)
        params = st.session_state.get("search_params") or {}
        if notice:
            st.warning(notice)
            render_glossary()
            return
        unknown: list[str] = []
        for value in (params.get("origin"), params.get("destination")):
            text = str(value or "").strip()
            if len(text) >= 2 and not stop_suggestions(api_base(), text, limit=50):
                unknown.append(f"„{text}“")
        if not params.get("origin") or not params.get("destination"):
            st.warning(
                "Bitte wähle Start und Ziel. Nutze die Vorschläge beim Tippen "
                "oder klicke oben auf eine Beispielstrecke."
            )
        elif str(params.get("origin")).strip().lower() == str(
            params.get("destination")
        ).strip().lower():
            st.warning(
                "Start und Ziel sind identisch. Bitte wähle zwei verschiedene Bahnhöfe."
            )
        elif unknown:
            st.warning(
                f"Diese Bahnhöfe wurden nicht erkannt: {', '.join(unknown)}. "
                "Bitte wähle einen Vorschlag aus der Liste oder einen größeren Bahnhof."
            )
        else:
            if db_mode:
                st.warning(
                    "Keine **direkte** Verbindung gefunden. Die DB-API liefert nur "
                    "Direktverbindungen – versuche größere Bahnhöfe (z. B. „Berlin Hbf“, "
                    "„Köln Hbf“), eine andere Uhrzeit oder einen anderen Tag."
                )
            else:
                st.warning(
                    "Keine Verbindungen gefunden. Prüfe die Schreibweise der Bahnhöfe, "
                    "wähle einen größeren Bahnhof oder ändere Uhrzeit und Datum."
                )
        if db_mode:
            st.caption(
                "Tipp: „Frankfurt Hbf → Hamburg Hbf“ oder „München Hbf → Berlin Hbf“ "
                "haben fast immer direkte Züge."
            )
        else:
            st.caption("Tipp: „Fulda → Frankfurt Hbf“ funktioniert immer als Teststrecke.")
        render_glossary()
        return

    st.markdown(f"#### :material/list_alt: {len(results)} Verbindungen")
    st.markdown(
        ":green-badge[viele Plätze frei] :orange-badge[wenige Plätze] "
        ":red-badge[ausgebucht] – tippe auf „Sitzplatz wählen“."
    )
    params = st.session_state.get("search_params") or {}
    st.caption(
        f"{params.get('origin') or 'Alle Bahnhöfe'} → "
        f"{params.get('destination') or 'Alle Bahnhöfe'} · "
        f"{params.get('date') or 'heute'} · "
        f"ab {hhmm(results[0].get('departureTime'))} · "
        f"bis {hhmm(results[-1].get('arrivalTime'), True)}"
    )
    for index, row in enumerate(results):
        connection_card(row, index)

    if not st.session_state.get("token"):
        st.caption(
            ":material/info: Suchen kannst du ohne Anmeldung. Zum Reservieren "
            "bitte oben rechts anmelden."
        )

    if any(not row.get("trainNumber") for row in results):
        st.caption("Zugnummern werden im Hintergrund geladen. Bei Bedarf aktualisieren.")
        st.button(
            "Ergebnisse aktualisieren",
            icon=":material/refresh:",
            on_click=refresh_search,
        )

    render_glossary()


def render_trip_progress() -> None:
    hold = st.session_state.get("hold")
    ticket = st.session_state.get("ticket")
    if ticket:
        step, text = 4, "Schritt 4 von 4 · Ticket anzeigen und einsteigen"
    elif hold:
        step, text = 3, "Schritt 3 von 4 · Reservierung bezahlen"
    else:
        step, text = 2, "Schritt 2 von 4 · Teilstrecke und Sitzplatz wählen"
    st.progress(step / 4, text=text)


def view_trip() -> None:
    ctx = st.session_state.get("trip_ctx")
    if not ctx:
        st.info("Noch keine Fahrt ausgewählt. Starte mit einer Suche.")
        st.button("Zur Suche", icon=":material/search:", on_click=go_search)
        return

    try:
        details = fetch_trip(api_base(), ctx["id"], ctx.get("date"))
    except (requests.RequestException, ApiError) as exc:
        report_error(ApiError(f"Backend nicht erreichbar ({api_base()}): {exc}"))
        return

    st.button(
        "Zurück zu den Ergebnissen",
        icon=":material/arrow_back:",
        on_click=go_search,
    )
    st.subheader(
        f":material/train: {train_label(details)} · "
        f"{details.get('originStop')} → {details.get('destinationStop')}"
    )
    st.caption(
        f"Abfahrt {hhmm(details.get('departureTime'))} · "
        f"Ankunft {hhmm(details.get('arrivalTime'), True)} · "
        f"{len(details.get('stops') or [])} Halte"
    )
    render_trip_progress()
    st.html(stops_html(details))

    with st.expander("So liest du den Wagenplan", icon=":material/help:"):
        st.markdown(
            "1. **Teilstrecke wählen:** Plätze werden für jeden Abschnitt "
            "einzeln vergeben.\n"
            "2. **Farbe beachten:** :green-badge[frei] · "
            ":orange-badge[gerade reserviert] · :red-badge[gebucht]\n"
            "3. **Platz antippen**, dann „Sitzplatz reservieren“.\n"
            "4. **Bezahlen:** Der Platz ist nur wenige Minuten für dich gesperrt.\n"
            "5. **QR-Ticket** beim Einstieg zeigen – fertig."
        )

    realtime_panel(ctx["id"], ctx.get("date"))

    st.markdown("#### :material/event_seat: Wagen & Sitzplätze")
    hold_panel()
    availability_panel(ctx["id"], details.get("segments") or [], ctx.get("segment_id"))

    with st.expander("Demo: anderen Passagier kaufen lassen"):
        st.caption(
            "Simuliert den Kauf eines Sitzplatzes in der aktuell gewählten "
            "Teilstrecke – gut, um die Live-Aktualisierung zu sehen."
        )
        if st.button("Demo-Kauf auslösen", icon=":material/person_add:"):
            segment_key = f"segment_{ctx['id']}"
            segment_id = st.session_state.get(segment_key)
            if not segment_id:
                st.warning("Bitte zuerst eine Teilstrecke wählen.")
            else:
                try:
                    result = api(
                        "/api/demo/purchase",
                        "POST",
                        json={"tripId": ctx["id"], "segmentId": segment_id},
                    )
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    fetch_availability.clear()
                    flash(
                        "ok",
                        f"{result.get('passengerEmail')} hat Wagen {result.get('carNumber')} · "
                        f"Platz {result.get('seatNumber')} auf {result.get('segmentFrom')} → "
                        f"{result.get('segmentTo')} gekauft.",
                    )
                    st.rerun()


def view_bookings() -> None:
    if not st.session_state.get("token"):
        st.info("Bitte oben rechts anmelden, um deine Buchungen zu sehen.")
        st.button(
            "Zur Suche",
            icon=":material/search:",
            on_click=go_search,
        )
        return
    st.subheader(":material/confirmation_number: Meine Buchungen")
    st.caption(
        "Jede Buchung gilt für eine Teilstrecke. QR-Ticket öffnen, einchecken "
        "oder stornieren – solange du nicht eingecheckt hast."
    )
    if st.button("Aktualisieren", icon=":material/refresh:"):
        st.rerun()
    try:
        bookings = api("/api/bookings", auth=True)
    except ApiError as exc:
        report_error(exc)
        return
    if not bookings:
        st.info(
            "Noch keine Buchungen. Suche eine Verbindung und sichere dir einen "
            "Sitzplatz auf deiner Teilstrecke."
        )
        st.button(
            "Erste Buchung starten",
            type="primary",
            icon=":material/search:",
            on_click=go_search,
        )
        return

    for booking in bookings:
        trip = booking.get("trip") or {}
        segment = booking.get("segment") or {}
        seat = booking.get("seat") or {}
        status = booking.get("status") or "–"
        with st.container(border=True):
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            with c1:
                st.markdown(
                    f"**{trip.get('routeName') or 'Zug'} · {trip.get('originStop')} → "
                    f"{trip.get('destinationStop')}**"
                )
                st.caption(
                    f"{segment.get('from')} → {segment.get('to')} · "
                    f"{hhmm(segment.get('departureTime'))}–{hhmm(segment.get('arrivalTime'), True)} · "
                    f"Wagen {seat.get('carNumber', '–')} · Platz {seat.get('seatNumber', '–')} · "
                    f"{fmt_money(booking.get('totalAmount'))} · erstellt {fmt_datetime(booking.get('createdAt'))}"
                )
            with c2:
                st.badge(
                    status_label(booking),
                    color=STATUS_COLORS.get(status, "gray"),
                )
                if booking.get("checkedInAt"):
                    st.caption(f"Check-in {fmt_clock(booking.get('checkedInAt'))}")
                if booking.get("failureReason"):
                    st.caption(booking["failureReason"])

            if booking.get("qrPayload"):
                with st.expander("Boarding Pass (QR)"):
                    st.caption("Beim Einstieg vorzeigen – der Code ist dein Ticket.")
                    st.code(booking["qrPayload"], language=None)

            actions = st.columns(3)
            if status in ("PENDING_PAYMENT", "CONFIRMED"):
                with actions[0]:
                    if st.button(
                        "Stornieren",
                        icon=":material/close:",
                        key=f"cancel_{booking['id']}",
                        width="stretch",
                        help="Gibt den Sitzplatz wieder frei.",
                    ):
                        cancel_booking(booking["id"])
            if status == "CONFIRMED" and not booking.get("isCheckedIn"):
                with actions[1]:
                    if st.button(
                        "Einsteigen (Check-in)",
                        icon=":material/how_to_reg:",
                        key=f"checkin_{booking['id']}",
                        width="stretch",
                        help="Bestätigt, dass du mitfährst.",
                    ):
                        check_in_booking(booking["id"], booking.get("qrPayload"))


def admin_rows(bookings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for booking in bookings:
        trip = booking.get("trip") or {}
        segment = booking.get("segment") or {}
        seat = booking.get("seat") or {}
        user = booking.get("user") or {}
        rows.append(
            {
                "Buchung": str(booking.get("id", ""))[:8],
                "Status": status_label(booking),
                "Zug": trip.get("routeName") or "–",
                "Strecke": f"{segment.get('from', '–')} → {segment.get('to', '–')}",
                "Sitz": f"{seat.get('carNumber', '–')} / {seat.get('seatNumber', '–')}",
                "Betrag": fmt_money(booking.get("totalAmount")),
                "Benutzer": user.get("email") or "–",
                "Läuft ab": fmt_datetime(booking.get("expiresAt")),
                "Erstellt": fmt_datetime(booking.get("createdAt")),
            }
        )
    return rows


def view_admin() -> None:
    user = st.session_state.get("user") or {}
    if user.get("role") != "ADMIN":
        st.warning("Dieser Bereich ist nur für Administratoren.")
        return
    st.subheader(":material/admin_panel_settings: Admin")
    st.caption(
        "Offene Reservierungen laufen nach Ablauf automatisch aus. Alle Zeiten "
        "in Ortszeit."
    )
    try:
        pending = api("/api/admin/pending-bookings", auth=True)
        all_bookings = api("/api/admin/bookings", auth=True)
    except ApiError as exc:
        report_error(exc)
        return

    pending_tab, all_tab = st.tabs(
        [f"Offene Reservierungen ({len(pending)})", f"Alle Buchungen ({len(all_bookings)})"]
    )
    with pending_tab:
        if not pending:
            st.caption("Keine offenen Reservierungen.")
        else:
            st.dataframe(admin_rows(pending), hide_index=True, width="stretch")
    with all_tab:
        if not all_bookings:
            st.caption("Keine Buchungen.")
        else:
            st.dataframe(admin_rows(all_bookings), hide_index=True, width="stretch")


def render_auth_control() -> None:
    token = st.session_state.get("token")
    user = st.session_state.get("user") or {}
    if token:
        st.markdown(f"**{user.get('fullName') or user.get('email') or 'Angemeldet'}**")
        st.caption(
            f"{user.get('email') or ''}"
            + (" · Admin" if user.get("role") == "ADMIN" else "")
        )
        st.button(
            "Abmelden",
            icon=":material/logout:",
            on_click=logout,
            width="stretch",
        )
        return
    with st.popover("Anmelden", icon=":material/login:", width="stretch"):
        st.caption(
            "Die Demo-Zugangsdaten sind schon eingetragen. Ein Konto brauchst du "
            "nur zum Reservieren – Suchen ist frei."
        )
        with st.form("login_form"):
            email = st.text_input("E-Mail", value="demo@trenes.local")
            password = st.text_input("Passwort", value="demo1234", type="password")
            login = st.form_submit_button("Anmelden", type="primary", width="stretch")
            register = st.form_submit_button("Konto erstellen", width="stretch")
        if login:
            do_auth("/api/auth/login", email, password)
        if register:
            do_auth("/api/auth/register", email, password)
        if st.button(
            "Demo-Anmeldung",
            icon=":material/person:",
            width="stretch",
            help="Meldet dich sofort als demo@trenes.local an.",
        ):
            do_auth("/api/auth/login", "demo@trenes.local", "demo1234")


def render_header() -> None:
    base = api_base()
    try:
        health = fetch_health(base)
    except Exception:
        health = None
    with st.container(border=True):
        brand, status, account = st.columns([2.7, 1.8, 1.5], vertical_alignment="center")
        with brand:
            st.markdown("## :material/train: Züge MVP")
            st.caption("Deine Reise durch Deutschland · Sitzplätze pro Teilstrecke")
        with status:
            if health:
                if st.session_state.get("demo_mode"):
                    st.badge("Demo-Modus", color="orange", icon=":material/science:")
                    st.caption("Beispieldaten · läuft ohne Backend")
                    st.button(
                        "Backend verbinden",
                        icon=":material/refresh:",
                        on_click=retry_backend,
                        width="stretch",
                        help="Prüft die Verbindung zur konfigurierten API erneut.",
                    )
                elif st.session_state.get("db_mode"):
                    st.badge("DB Live", color="green", icon=":material/database:")
                    st.caption(
                        "Fahrplan: DB API Marketplace · Sitze & Preise simuliert"
                    )
                else:
                    st.badge("API online", color="green", icon=":material/cloud_done:")
                    st.caption(
                        f"Zahlungen: {health.get('payments', '–')} · "
                        f"E-Mail: {health.get('mail', '–')}"
                    )
            else:
                st.badge("API offline", color="red", icon=":material/cloud_off:")
                st.caption("Backend unter `backend/` starten (Port 3000).")
        with account:
            render_auth_control()


def render_nav() -> None:
    nav_col, stats_col = st.columns([2.1, 3.6], vertical_alignment="center")
    with nav_col:
        st.segmented_control(
            "Bereich",
            list(PAGES),
            key="page",
            label_visibility="collapsed",
            width="stretch",
            wrap=True,
        )
    with stats_col:
        try:
            stats = fetch_stats(api_base())
        except Exception:
            st.caption("Systemdaten nicht verfügbar.")
        else:
            with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
                if st.session_state.get("db_mode"):
                    st.badge(
                        f"{fmt_int(stats.get('stations'))} Bahnhöfe",
                        color="gray",
                        icon=":material/database:",
                    )
                    st.badge("Fahrplan: DB API", color="gray")
                    st.badge("Sitze simuliert", color="gray")
                else:
                    st.badge(f"{fmt_int(stats.get('trips'))} Fahrten", color="gray")
                    st.badge(f"{fmt_int(stats.get('segments'))} Teilstrecken", color="gray")
                    st.badge(f"{fmt_int(stats.get('seats'))} Sitze", color="gray")
                    if stats.get("lastGtfsIngestedAt"):
                        imported = fmt_datetime(stats["lastGtfsIngestedAt"]).split(" ")[0]
                        st.badge(f"GTFS-Import {imported}", color="gray")
    hint = PAGE_HINTS.get(st.session_state.get("page") or "Suche")
    if hint:
        st.caption(f":material/lightbulb: {hint}")


st.set_page_config(
    page_title="Züge MVP · Sitzplätze auf Teilstrecken",
    page_icon=":material/train:",
    layout="centered",
    initial_sidebar_state="collapsed",
)

init_state()
ensure_backend()
validate_session()
render_flash()
render_header()
render_nav()

page = st.session_state.get("page") or "Suche"
nav = PAGES.get(page, NAV_SEARCH)
if nav == NAV_SEARCH:
    view_search()
elif nav == NAV_TRIP:
    view_trip()
elif nav == NAV_BOOKINGS:
    view_bookings()
elif nav == NAV_ADMIN:
    view_admin()
