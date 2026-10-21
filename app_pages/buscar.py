"""Página 1 · búsqueda de conexiones."""

from __future__ import annotations

import random

import streamlit as st

from seatnow import inventory
from seatnow.format import fmt_clock, fmt_duration, fmt_money, local_today
from seatnow.loader import load_feed
from seatnow.search import search_trains
from seatnow.ui import open_trip

feed = load_feed()

st.subheader("Verbindung suchen")
st.caption("Suche eine Verbindung und öffne den Wagenplan, um einen Sitzplatz zu reservieren.")

stop_options = ["", *feed.stop_names]
today = local_today().date()


def _swap() -> None:
    st.session_state["search_origin"], st.session_state["search_destination"] = (
        st.session_state.get("search_destination", ""),
        st.session_state.get("search_origin", ""),
    )


def _random() -> None:
    stops = feed.stop_names
    origin = random.choice(stops)
    st.session_state["search_origin"] = origin
    st.session_state["search_destination"] = random.choice([s for s in stops if s != origin])
    st.session_state["_do_search"] = True


def _label(option: str) -> str:
    return option or "Beliebiger Bahnhof"


cols = st.columns([3, 0.5, 3, 2])
origin = cols[0].selectbox(
    "Abfahrt ab", stop_options, key="search_origin", format_func=_label
)
cols[1].button("⇄", help="Abfahrt und Ankunft tauschen", on_click=_swap, key="swap_btn")
destination = cols[2].selectbox(
    "Ankunft nach", stop_options, key="search_destination", format_func=_label
)
day = cols[3].date_input("Datum", value=today, key="search_date")

row = st.columns([2, 1.6, 1.6, 2.4])
time_type = row[0].segmented_control(
    "Zeittyp", ["Abfahrt ab", "Ankunft bis"], default="Abfahrt ab", key="search_time_type"
)
time_from = row[1].time_input("Von", value=None, key="search_time_from")
time_to = row[2].time_input("Bis", value=None, key="search_time_to")
with row[3]:
    st.space("medium")
    with st.container(horizontal=True):
        clicked_search = st.button("Zug finden", type="primary", icon=":material/search:", key="search_btn")
        st.button("Zufall", icon=":material/casino:", on_click=_random, key="random_btn")

trigger = st.session_state.pop("_do_search", False)
if clicked_search or trigger:
    st.session_state["search_params"] = {
        "origin": origin or None,
        "destination": destination or None,
        "day": day,
        "time_type": "departure" if time_type == "Abfahrt ab" else "arrival",
        "time_from": time_from.hour * 60 + time_from.minute if time_from else None,
        "time_to": time_to.hour * 60 + time_to.minute if time_to else None,
    }
    st.session_state["search_results"] = search_trains(
        feed, **st.session_state["search_params"]
    )

results = st.session_state.get("search_results") or []
params = st.session_state.get("search_params")

if not results:
    st.info(
        "Noch keine Ergebnisse. Wähle Abfahrt und Ankunft und starte die Suche.",
        icon=":material/travel_explore:",
    )
    st.stop()

st.caption(
    f"{len(results)} Verbindung(en) · "
    f"{params['origin'] or 'beliebiger Bahnhof'} → {params['destination'] or 'beliebiger Bahnhof'}"
)

rows = []
for result in results:
    state = inventory.availability_for_segment(feed, result["segment_id"])
    rows.append(
        {
            "Zug": f"{result['train']} · {result['route_name']}",
            "Verbindung": f"{result['origin_stop']} → {result['destination_stop']}",
            "Ab": fmt_clock(result["departure"]),
            "An": fmt_clock(result["arrival"]),
            "Dauer": fmt_duration(result["duration_minutes"]),
            "Frei": inventory.count_available(state),
            "Preis": fmt_money(result["price"]),
        }
    )

event = st.dataframe(
    rows,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="results_table",
    column_config={
        "Frei": st.column_config.NumberColumn("Freie Sitze", format="%d"),
        "Ab": st.column_config.TextColumn("Abfahrt"),
        "An": st.column_config.TextColumn("Ankunft"),
    },
)

selected = event.selection.rows if event and event.selection else []
if selected:
    chosen = results[selected[0]]
    st.markdown(
        f"**Ausgewählt:** {chosen['train']} {chosen['route_name']} · "
        f"{chosen['origin_stop']} → {chosen['destination_stop']} · "
        f"{fmt_clock(chosen['departure'])} – {fmt_clock(chosen['arrival'])} · "
        f"{fmt_money(chosen['price'])}"
    )
    if st.button("Wagenansicht öffnen", type="primary", icon=":material/event_seat:"):
        open_trip(chosen["trip_id"], chosen["segment_id"])
        st.switch_page("app_pages/wagen.py")
elif len(results) == 1:
    st.caption("Tipp: Wähle eine Zeile in der Tabelle aus, um den Wagenplan zu öffnen.")
