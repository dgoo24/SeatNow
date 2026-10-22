"""Componentes de interfaz reutilizables (puerto visual de `index.html`)."""

from __future__ import annotations

import time
from datetime import date
from html import escape
from typing import Any

import streamlit as st

from seatnow import gtfs, inventory, realtime, store
from seatnow.config import CAR_COUNT, SEATS_PER_CAR
from seatnow.format import (
    fmt_clock,
    fmt_money,
    local_now,
    local_today,
    seat_label,
    status_label,
    train_label,
)
from seatnow.theme import (
    chips_html,
    legend_html,
    live_box_html,
    seat_css,
)

SELECTED_SEAT_KEY = "selected_seat_id"
SELECTED_TRIP_KEY = "selected_trip_id"
SELECTED_SEGMENT_KEY = "selected_segment_id"
WAGEN_DEADLINE_KEY = "wagen_deadline"
WAGEN_MAX_SECONDS = 180


# --------------------------------------------------------------------------- #
# Sesión / autenticación
# --------------------------------------------------------------------------- #

def init_state() -> None:
    st.session_state.setdefault("user", None)
    st.session_state.setdefault(SELECTED_TRIP_KEY, None)
    st.session_state.setdefault(SELECTED_SEGMENT_KEY, None)
    st.session_state.setdefault(SELECTED_SEAT_KEY, None)
    st.session_state.setdefault("search_results", [])
    st.session_state.setdefault("search_params", None)
    st.session_state.setdefault(WAGEN_DEADLINE_KEY, None)


def current_user() -> dict[str, Any] | None:
    return st.session_state.get("user")


def sign_in(email: str, password: str) -> bool:
    user = store.authenticate(email, password)
    if user is None:
        return False
    st.session_state["user"] = user
    return True


def sign_out() -> None:
    st.session_state["user"] = None


def require_user() -> dict[str, Any]:
    user = current_user()
    if user is None:
        st.warning("Bitte melde dich an, um eine Reservierung anzulegen.", icon=":material/login:")
        st.caption(
            "Demo-Konten: `pasajero1@trenes.local` · `demo1234` — "
            "Admin: `admin@trenes.local` · `admin1234`"
        )
        st.stop()
    return user


def require_admin() -> dict[str, Any]:
    user = require_user()
    if user.get("role") != "ADMIN":
        st.error("Diese Seite ist Administratoren vorbehalten.", icon=":material/lock:")
        st.stop()
    return user


# --------------------------------------------------------------------------- #
# Barra lateral
# --------------------------------------------------------------------------- #

def render_sidebar(feed: gtfs.Feed, source: str = "demo", container: Any = None) -> None:
    """Barra lateral. `source` indica de dónde salen los datos ('db' o 'demo')."""
    target = container if container is not None else st.sidebar
    with target:
        st.markdown("### :material/train: SeatNow")
        st.caption("Sitzplatzreservierung · Teilstrecken")

        user = current_user()
        if user:
            st.markdown(f"**{user['full_name']}**")
            st.caption(user["email"])
            if st.button("Abmelden", icon=":material/logout:", width="stretch"):
                sign_out()
                st.rerun()
        else:
            with st.expander("Anmelden / registrieren", icon=":material/login:"):
                email = st.text_input(
                    "E-Mail", key="auth_email", placeholder="pasajero1@trenes.local"
                )
                password = st.text_input("Passwort", type="password", key="auth_password")
                with st.container(horizontal=True):
                    if st.button("Anmelden", icon=":material/login:", width="stretch"):
                        if sign_in(email, password):
                            st.rerun()
                        else:
                            st.error("E-Mail oder Passwort ist falsch.")
                    if st.button("Registrieren", icon=":material/person_add:", width="stretch"):
                        if store.create_user(email, password, email.split("@")[0]):
                            sign_in(email, password)
                            st.rerun()
                        else:
                            st.error("Diese E-Mail ist bereits registriert.")
            st.caption("Demo: `pasajero1@trenes.local` / `demo1234`")

        st.space("small")
        st.markdown("**Bestand**")
        seats = len(feed.trips) and CAR_COUNT * SEATS_PER_CAR
        st.caption(
            f"{len(feed.trips)} Züge · {len(feed.segments)} Teilstrecken · {seats} Sitzplätze/Zug"
        )
        st.caption(
            "Datenquelle: DB-Live-Fahrplan"
            if source == "db"
            else "Datenquelle: GTFS-Demofeed (Spanien)"
        )
        st.caption("Zahlung: mock · E-Mail: deaktiviert")
        st.caption(f"Stand: {local_now():%d.%m.%Y %H:%M}")


# --------------------------------------------------------------------------- #
# Vista de viaje
# --------------------------------------------------------------------------- #

def trip_header_html(trip: gtfs.Trip, segment: gtfs.Segment) -> str:
    label = train_label(trip.route_name)
    return (
        '<div class="sn-trip-top"><div>'
        '<p class="sn-card-title">Wagenansicht · Teilstrecke</p>'
        f'<h2 class="sn-trip-title">{escape(label)} · {escape(trip.route_name)}</h2>'
        f'<div class="sn-trip-meta">Abfahrt {escape(fmt_clock(segment.departure))} · '
        f"Ankunft {escape(fmt_clock(segment.arrival))} · {len(trip.stops)} Halte</div>"
        "</div></div>"
    )


def render_trip_header(trip: gtfs.Trip, segment: gtfs.Segment) -> None:
    st.html(trip_header_html(trip, segment))


@st.fragment(run_every=1.0)
def render_countdown() -> None:
    """Cuenta atrás de la vista de wagon (máx. 3 minutos, como en el HTML)."""
    deadline = st.session_state.get(WAGEN_DEADLINE_KEY)
    if not deadline:
        return
    remaining = max(int(deadline - time.time()), 0)
    minutes, seconds = divmod(remaining, 60)
    klass = "sn-countdown warn" if remaining <= 60 else "sn-countdown"
    st.html(f'<div class="{klass}">{minutes}:{seconds:02d}</div>')


def render_live_box(trip: gtfs.Trip, day: date) -> realtime.LiveStatus:
    status = realtime.live_status(trip, day)
    next_stops = realtime.stop_delays(trip, day, status)[:6]
    st.html(live_box_html(status, next_stops, fmt_clock(local_now().strftime("%H:%M:%S"))))
    return status


def render_stops(
    trip: gtfs.Trip, segment: gtfs.Segment, status: realtime.LiveStatus | None = None
) -> None:
    day = local_today().date()
    delays = {
        row["name"]: row
        for row in (realtime.stop_delays(trip, day, status) if status else [])
    }
    items: list[tuple[str, bool]] = []
    for stop in trip.stops:
        delay = delays.get(stop.stop_name, {}).get("delay_minutes", 0)
        suffix = f" (+{delay})" if delay else ""
        focus = stop.stop_name in (segment.origin_stop, segment.destination_stop)
        items.append((f"{stop.stop_name} {fmt_clock(stop.departure)}{suffix}", focus))
    st.html(chips_html(items))


def render_legend() -> None:
    st.html(legend_html())


# --------------------------------------------------------------------------- #
# Mapa de asientos
# --------------------------------------------------------------------------- #

def _select_seat(seat_id: str) -> None:
    st.session_state[SELECTED_SEAT_KEY] = seat_id


def render_seat_map(
    feed: gtfs.Feed,
    segment_id: str,
    state: dict[str, dict[str, Any]],
    key_prefix: str = "seat",
) -> None:
    segment = feed.segments[segment_id]
    statuses: dict[str, str] = {}
    for car in range(1, CAR_COUNT + 1):
        for number in range(1, SEATS_PER_CAR + 1):
            seat_id = f"{segment.trip_id}:{car}:{number:02d}"
            info = state.get(seat_id, {"status": "AVAILABLE", "reservations": []})
            statuses[f"{key_prefix}_{car}_{number}"] = info["status"]
    st.html(seat_css(statuses))

    for car in range(1, CAR_COUNT + 1):
        with st.container(key=f"wagon_{car}"):
            st.html(f'<div class="sn-wagon-head">Wagen {car}</div>')
            with st.container(key=f"snseats_{car}"):
                for row_start in range(0, SEATS_PER_CAR, 5):
                    cols = st.columns(5, gap="small")
                    for offset in range(5):
                        number = row_start + offset + 1
                        seat_id = f"{segment.trip_id}:{car}:{number:02d}"
                        info = state.get(seat_id, {"status": "AVAILABLE", "reservations": []})
                        status = info["status"]
                        if status == "HELD":
                            icon, help_text = ":material/hourglass_top:", "Reserviert · Zahlung offen"
                        elif status == "BOOKED":
                            icon, help_text = ":material/lock:", "Gebucht"
                        else:
                            icon, help_text = None, "Frei in dieser Teilstrecke"
                        cols[offset].button(
                            f"{number:02d}",
                            key=f"{key_prefix}_{car}_{number}",
                            icon=icon,
                            type="secondary",
                            disabled=status != "AVAILABLE",
                            width="stretch",
                            help=help_text,
                            on_click=_select_seat,
                            args=(seat_id,),
                        )


# --------------------------------------------------------------------------- #
# Panel de reserva
# --------------------------------------------------------------------------- #

def render_reservation_panel(
    feed: gtfs.Feed,
    segment: gtfs.Segment,
    trip: gtfs.Trip,
    user: dict[str, Any] | None,
) -> None:
    seat_id = st.session_state.get(SELECTED_SEAT_KEY)
    with st.container(border=True):
        if not seat_id:
            st.markdown("**Reservierung**")
            st.caption("Wähle im Sitzplan einen freien Platz, um die Reservierung zu starten.")
            return

        state = inventory.availability_for_segment(feed, segment.id)
        info = state.get(seat_id, {"status": "AVAILABLE", "reservations": []})
        if info["status"] != "AVAILABLE":
            st.warning(f"{seat_label(seat_id)} ist nicht mehr frei.", icon=":material/block:")
            return

        st.markdown("**Reservierung**")
        st.html(
            '<div class="sn-kv"><b>Teilstrecke</b>'
            f"<span>{escape(segment.origin_stop)} → {escape(segment.destination_stop)}</span></div>"
            '<div class="sn-kv"><b>Zeit</b>'
            f"<span>{escape(fmt_clock(segment.departure))} – {escape(fmt_clock(segment.arrival))}</span></div>"
            '<div class="sn-kv"><b>Entfernung</b>'
            f"<span>{segment.distance_km:.0f} km</span></div>"
            '<div class="sn-kv"><b>Sitzpreis</b>'
            f"<span>{escape(fmt_money(segment.price))}</span></div>"
            '<div class="sn-kv"><b>Sitzplatz</b>'
            f"<span>{escape(seat_label(seat_id))}</span></div>"
        )

        if user is None:
            st.info("Zum Reservieren bitte anmelden.", icon=":material/login:")
            return

        if st.button(
            "Sitzplatz reservieren", type="primary", icon=":material/event_seat:", width="stretch"
        ):
            try:
                booking = inventory.hold_seat(
                    feed,
                    user_id=user["id"],
                    trip_id=trip.trip_id,
                    seat_id=seat_id,
                    segment_id=segment.id,
                )
            except inventory.InventoryError as error:
                st.error(str(error), icon=":material/error:")
            else:
                st.session_state["active_booking"] = booking["id"]
                st.session_state[SELECTED_SEAT_KEY] = None
                st.toast("Sitzplatz reserviert · Zahlungsfrist läuft", icon=":material/check_circle:")
                st.rerun()

        st.caption("Der Platz wird für 3 Minuten gehalten. Ohne Zahlung wird er automatisch freigegeben.")


def render_active_booking(feed: gtfs.Feed, user: dict[str, Any] | None) -> None:
    """Muestra el hold activo con cuenta atrás y el botón de pago."""
    booking_id = st.session_state.get("active_booking")
    if not booking_id:
        return
    booking = store.get_booking(booking_id)
    if booking is None:
        st.session_state.pop("active_booking", None)
        return
    if user is None or booking["user_id"] != user["id"]:
        return

    segment = feed.segments.get(booking["segment_id"])
    if segment is None:
        return

    with st.container(border=True):
        st.markdown("**Offene Reservierung**")
        cols = st.columns(3)
        cols[0].metric("Buchung", booking["id"][:8].upper())
        cols[1].metric("Sitzplatz", seat_label(booking["seat_id"]))
        cols[2].metric("Betrag", fmt_money(booking["total_amount"]))

        if booking["status"] == "PENDING_PAYMENT":
            remaining = _remaining_seconds(booking.get("expires_at"))
            st.progress(
                min(max(remaining / 180, 0.0), 1.0),
                text=f"Zahlungsfrist: {remaining} s",
            )
            with st.container(horizontal=True):
                if st.button("Jetzt bezahlen", type="primary", icon=":material/payments:"):
                    try:
                        inventory.checkout(feed, booking_id=booking["id"], user_id=user["id"])
                    except inventory.InventoryError as error:
                        st.error(str(error), icon=":material/error:")
                    else:
                        st.session_state.pop("active_booking", None)
                        st.toast("Zahlung erfolgreich · Buchung bestätigt", icon=":material/check_circle:")
                        st.rerun()
                if st.button("Reservierung stornieren", icon=":material/cancel:"):
                    inventory.cancel_booking(booking["id"], user_id=user["id"])
                    st.session_state.pop("active_booking", None)
                    st.rerun()
        elif booking["status"] == "CONFIRMED":
            st.success(
                f"Buchung bestätigt · QR-Code {booking['qr_payload']}",
                icon=":material/confirmation_number:",
            )


def _remaining_seconds(expires_at: str | None) -> int:
    from datetime import datetime, timezone

    if not expires_at:
        return 0
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return 0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return max(int((deadline - datetime.now(timezone.utc)).total_seconds()), 0)


def open_trip(trip_id: str, segment_id: str | None = None) -> None:
    """Selecciona un viaje (y tramo) antes de saltar a la página del Wagen."""
    st.session_state["wagen_trip"] = trip_id
    if segment_id:
        st.session_state["wagen_segment"] = segment_id
    st.session_state[SELECTED_SEAT_KEY] = None
    st.session_state[SELECTED_TRIP_KEY] = trip_id
    st.session_state[SELECTED_SEGMENT_KEY] = segment_id
    st.session_state[WAGEN_DEADLINE_KEY] = time.time() + WAGEN_MAX_SECONDS


def status_badge(status: str) -> str:
    mapping = {
        "CONFIRMED": ":green-badge[Bestätigt]",
        "PENDING_PAYMENT": ":orange-badge[Zahlung offen]",
        "CANCELLED": ":gray-badge[Storniert]",
        "FAILED": ":gray-badge[Abgelaufen]",
        "NO_SHOW": ":red-badge[No-Show]",
        "CANCEL_PENDING_REFUND": ":red-badge[Erstattung offen]",
    }
    return mapping.get(status, f":gray-badge[{status_label(status)}]")
