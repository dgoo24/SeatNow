"""Página 2 · Wagenansicht y mapa de asientos (tarjeta del viaje)."""

from __future__ import annotations

import random
import time

import streamlit as st

from seatnow import inventory, store
from seatnow.format import fmt_clock, fmt_money, local_today, train_label, trip_title
from seatnow.loader import active_feed
from seatnow.ui import (
    WAGEN_DEADLINE_KEY,
    WAGEN_MAX_SECONDS,
    current_user,
    render_active_booking,
    render_countdown,
    render_legend,
    render_live_box,
    render_reservation_panel,
    render_seat_map,
    render_stops,
    trip_header_html,
)

feed = active_feed()

trip_ids = list(feed.trips)
if not trip_ids:
    st.warning("No hay viajes cargados desde el feed GTFS.", icon=":material/error:")
    st.stop()

if st.session_state.get("wagen_trip") not in trip_ids:
    st.session_state["wagen_trip"] = trip_ids[0]
if not st.session_state.get(WAGEN_DEADLINE_KEY):
    st.session_state[WAGEN_DEADLINE_KEY] = time.time() + WAGEN_MAX_SECONDS

deadline = st.session_state[WAGEN_DEADLINE_KEY]
if time.time() > deadline:
    st.error(
        "Maximale Anzeigedauer der Wagenansicht (3 Min.) erreicht. Zurück zu den Ergebnissen.",
        icon=":material/timer_off:",
    )
    if st.button("Zurück zu den Ergebnissen", type="primary", icon=":material/arrow_back:"):
        st.switch_page("app_pages/buscar.py")
    st.stop()


def _on_trip_change() -> None:
    st.session_state["wagen_segment"] = None


def _trip_label(trip_id: str) -> str:
    trip = feed.trips[trip_id]
    train = trip.train_display or train_label(trip.route_name)
    route = trip.route_display or trip.route_name
    return (
        f"{trip_title(train, route)} · "
        f"{trip.origin_stop} → {trip.destination_stop} ({fmt_clock(trip.departure)})"
    )


def _segment_label(segment_id: str) -> str:
    segment = feed.segments[segment_id]
    return (
        f"{segment.origin_stop} → {segment.destination_stop} · "
        f"{fmt_clock(segment.departure)}–{fmt_clock(segment.arrival)} · "
        f"{fmt_money(segment.price)} ({segment.duration_minutes} Min.)"
    )


def _demo_purchase(segment_id: str) -> dict | None:
    """Compra de prueba en el tramo visible (como el botón 'Demo-Kauf' del HTML)."""
    passenger = store.get_user_by_email("pasajero1@trenes.local")
    if passenger is None:
        return None
    segment = feed.segments[segment_id]
    state = inventory.availability_for_segment(feed, segment_id)
    free = [seat for seat, info in state.items() if info["status"] == "AVAILABLE"]
    if not free:
        return None
    try:
        booking = inventory.hold_seat(
            feed,
            user_id=passenger["id"],
            trip_id=segment.trip_id,
            seat_id=random.choice(free),
            segment_id=segment_id,
            ttl_seconds=60,
        )
        return inventory.checkout(feed, booking_id=booking["id"])
    except inventory.InventoryError:
        return None


# El viaje y el tramo se fijan antes de dibujar los widgets para que la
# cabecera de la tarjeta ya muestre los datos correctos.
trip_id = st.session_state["wagen_trip"]
segments = feed.segments_by_trip.get(trip_id, [])
segment_ids = [segment.id for segment in segments]
if st.session_state.get("wagen_segment") not in segment_ids:
    st.session_state["wagen_segment"] = segment_ids[0] if segment_ids else None
segment_id = st.session_state["wagen_segment"]

with st.container(border=True):
    top = st.columns([4, 1], vertical_alignment="center")
    if segment_id:
        top[0].html(trip_header_html(feed.trips[trip_id], feed.segments[segment_id]))
    with top[1]:
        render_countdown()

    tools = st.columns([1.4, 1.4, 1.4], vertical_alignment="center")
    if tools[0].button("← Ergebnisse", icon=":material/arrow_back:", key="back_btn"):
        st.switch_page("app_pages/buscar.py")
    if tools[1].button("Karte aktualisieren", icon=":material/refresh:", key="refresh_btn"):
        st.rerun()
    if tools[2].button(
        "Demo-Kauf", type="primary", icon=":material/shopping_cart_checkout:", key="demo_btn"
    ):
        created = _demo_purchase(segment_id) if segment_id else None
        if created is None:
            st.warning("Keine freien Plätze in diesem Teilstrecke.", icon=":material/block:")
        else:
            st.toast(
                f"Demo-Kauf: Wagen {created['seat_id'].split(':')[1]} · "
                f"{created['seat_id'].split(':')[2]}",
                icon=":material/shopping_cart_checkout:",
            )
        st.rerun()

    st.selectbox(
        "Zug (Nummer)", trip_ids, key="wagen_trip", format_func=_trip_label, on_change=_on_trip_change
    )
    st.selectbox(
        "Teilstrecke (Sitzplätze gelten nur hierfür!)",
        segment_ids,
        key="wagen_segment",
        format_func=_segment_label,
    )

    if not segment_id:
        st.warning("Este viaje no tiene tramos disponibles.", icon=":material/error:")
        st.stop()

    trip = feed.trips[trip_id]
    segment = feed.segments[segment_id]

    status = render_live_box(trip, local_today().date())

    st.markdown("**Zughalte**")
    render_stops(trip, segment, status)

    render_legend()

    state = inventory.availability_for_segment(feed, segment_id)
    render_seat_map(feed, segment_id, state)

    st.space("small")
    render_active_booking(feed, current_user())
    render_reservation_panel(feed, segment, trip, current_user())
