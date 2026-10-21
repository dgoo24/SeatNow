"""Página 2 · Wagenansicht y mapa de asientos."""

from __future__ import annotations

import streamlit as st

from seatnow import inventory
from seatnow.format import fmt_clock, fmt_duration, fmt_money, local_today, train_label
from seatnow.loader import load_feed
from seatnow.ui import (
    current_user,
    render_active_booking,
    render_legend,
    render_live_box,
    render_reservation_panel,
    render_seat_map,
    render_stops,
    render_trip_header,
)

feed = load_feed()

st.subheader("Wagenansicht · Teilstrecke")

trip_ids = list(feed.trips)
if not trip_ids:
    st.warning("No hay viajes cargados desde el feed GTFS.", icon=":material/error:")
    st.stop()

if st.session_state.get("wagen_trip") not in trip_ids:
    st.session_state["wagen_trip"] = trip_ids[0]


def _on_trip_change() -> None:
    st.session_state["wagen_segment"] = None


def _trip_label(trip_id: str) -> str:
    trip = feed.trips[trip_id]
    return (
        f"{train_label(trip.route_name)} · {trip.origin_stop} → {trip.destination_stop}"
        f" ({fmt_clock(trip.departure)})"
    )


def _segment_label(segment_id: str) -> str:
    segment = feed.segments[segment_id]
    return (
        f"{segment.origin_stop} → {segment.destination_stop} · "
        f"{fmt_clock(segment.departure)}–{fmt_clock(segment.arrival)} · "
        f"{fmt_money(segment.price)} ({fmt_duration(segment.duration_minutes)})"
    )


trip_id = st.selectbox(
    "Zug", trip_ids, key="wagen_trip", format_func=_trip_label, on_change=_on_trip_change
)

segments = feed.segments_by_trip.get(trip_id, [])
segment_ids = [segment.id for segment in segments]
if st.session_state.get("wagen_segment") not in segment_ids:
    st.session_state["wagen_segment"] = segment_ids[0] if segment_ids else None

segment_id = st.selectbox(
    "Teilstrecke", segment_ids, key="wagen_segment", format_func=_segment_label
)

if not segment_id:
    st.warning("Este viaje no tiene tramos disponibles.", icon=":material/error:")
    st.stop()

trip = feed.trips[trip_id]
segment = feed.segments[segment_id]

render_trip_header(trip, segment)

status = render_live_box(trip, local_today().date())
render_stops(trip, segment, status)

st.markdown("**Sitzplan**")
render_legend()
state = inventory.availability_for_segment(feed, segment_id)
render_seat_map(feed, segment_id, state)

st.space("small")
render_active_booking(feed, current_user())
render_reservation_panel(feed, segment, trip, current_user())
