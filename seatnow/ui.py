"""Componentes de interfaz reutilizables."""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from seatnow import gtfs, inventory, realtime, store
from seatnow.config import CAR_COUNT, SEATS_PER_CAR
from seatnow.format import (
    fmt_clock,
    fmt_duration,
    fmt_money,
    local_now,
    local_today,
    seat_label,
    status_label,
    train_label,
)

SELECTED_SEAT_KEY = "selected_seat_id"
SELECTED_TRIP_KEY = "selected_trip_id"
SELECTED_SEGMENT_KEY = "selected_segment_id"


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
        st.caption("Demo-Konten: `pasajero1@trenes.local` · `demo1234` — Admin: `admin@trenes.local` · `admin1234`")
        st.stop()
    return user


def require_admin() -> dict[str, Any]:
    user = require_user()
    if user.get("role") != "ADMIN":
        st.error("Diese Seite ist Administratoren vorbehalten.", icon=":material/lock:")
        st.stop()
    return user


# --------------------------------------------------------------------------- #
# Cabecera y badges
# --------------------------------------------------------------------------- #

def render_sidebar(feed: gtfs.Feed) -> None:
    with st.sidebar:
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
                email = st.text_input("E-Mail", key="auth_email", placeholder="pasajero1@trenes.local")
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
        st.caption(f"{len(feed.trips)} Züge · {len(feed.segments)} Teilstrecken · {seats} Sitzplätze/Zug")
        st.caption("Zahlung: mock · E-Mail: deaktiviert")
        st.caption(f"Stand: {local_now():%d.%m.%Y %H:%M}")


def render_trip_header(trip: gtfs.Trip, segment: gtfs.Segment) -> None:
    label = train_label(trip.route_name)
    st.markdown(f"#### {label} · {trip.route_name}")
    cols = st.columns(4)
    cols[0].metric("Abfahrt", fmt_clock(segment.departure))
    cols[1].metric("Ankunft", fmt_clock(segment.arrival))
    cols[2].metric("Dauer", fmt_duration(segment.duration_minutes))
    cols[3].metric("Entfernung", f"{segment.distance_km:.0f} km")


def render_live_box(trip: gtfs.Trip, day: date) -> realtime.LiveStatus:
    status = realtime.live_status(trip, day)
    icon = {
        "error": ":material/cancel:",
        "warning": ":material/schedule:",
        "success": ":material/check_circle:",
    }[status.severity]
    with st.container(border=True):
        cols = st.columns([3, 1, 1])
        cols[0].markdown(f"{icon} **Live-Status** · {status.headline}")
        cols[1].markdown(f"Gleis **{status.platform}** (plan {status.planned_platform})")
        cols[2].markdown(f"Verspätung **{status.delay_minutes} Min.**")
        st.caption(status.message)
    return status


def render_stops(trip: gtfs.Trip, segment: gtfs.Segment, status: realtime.LiveStatus | None = None) -> None:
    delays = {row["name"]: row for row in (realtime.stop_delays(trip, local_today().date(), status) if status else [])}
    chips = []
    for stop in trip.stops:
        marker = "◀" if stop.sequence == segment.origin_sequence else ""
        marker += "▶" if stop.sequence == segment.destination_sequence else ""
        delay = delays.get(stop.stop_name, {}).get("delay_minutes", 0)
        suffix = f" (+{delay})" if delay else ""
        chips.append(f"{marker} **{stop.stop_name}** · {fmt_clock(stop.departure)}{suffix}")
    st.markdown(" · ".join(chips))


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
    for car in range(1, CAR_COUNT + 1):
        with st.container(border=True):
            st.markdown(f"**Wagen {car}**")
            for row_start in range(0, SEATS_PER_CAR, 5):
                cols = st.columns(5)
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
                        icon, help_text = None, "Frei"
                    cols[offset].button(
                        f"{number:02d}",
                        key=f"{key_prefix}_{car}_{number}",
                        icon=icon,
                        type="primary" if status == "AVAILABLE" else "secondary",
                        disabled=status != "AVAILABLE",
                        width="stretch",
                        help=help_text,
                        on_click=_select_seat,
                        args=(seat_id,),
                    )


def render_legend() -> None:
    st.markdown(
        ":blue-badge[frei] :orange-badge[reserviert] :red-badge[belegt]",
        help="Legende des Sitzplans",
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
        cols = st.columns(3)
        cols[0].metric("Sitzplatz", seat_label(seat_id))
        cols[1].metric("Teilstrecke", f"{segment.origin_stop} → {segment.destination_stop}")
        cols[2].metric("Preis", fmt_money(segment.price))

        if user is None:
            st.info("Zum Reservieren bitte anmelden.", icon=":material/login:")
            return

        if st.button("Sitzplatz reservieren", type="primary", icon=":material/event_seat:", width="stretch"):
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

        st.caption(
            "Der Platz wird für 3 Minuten gehalten. Ohne Zahlung wird er automatisch freigegeben."
        )


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
