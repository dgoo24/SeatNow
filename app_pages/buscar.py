"""Página 1 · búsqueda de conexiones (tarjeta "Verbindung suchen")."""

from __future__ import annotations

import random

import streamlit as st

from seatnow import inventory
from seatnow.format import fmt_clock, fmt_duration, fmt_money, local_today
from seatnow.loader import load_feed
from seatnow.search import search_trains
from seatnow.theme import (
    card_head,
    free_html,
    hint_html,
    route_html,
    time_html,
    train_kind_html,
)
from seatnow.ui import open_trip

feed = load_feed()

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


with st.container(border=True):
    st.html(card_head("Verbindung suchen"))

    cols = st.columns([3, 0.35, 3], vertical_alignment="bottom")
    origin = cols[0].selectbox(
        "Von", stop_options, key="search_origin", format_func=_label, placeholder="Startbahnhof"
    )
    cols[1].button("⇄", help="Abfahrt und Ankunft tauschen", on_click=_swap, key="swap_btn")
    destination = cols[2].selectbox(
        "Nach", stop_options, key="search_destination", format_func=_label, placeholder="Zielbahnhof"
    )

    meta = st.columns([1.5, 1.7, 1, 1])
    day = meta[0].date_input("Datum", value=today, key="search_date")
    time_type = meta[1].segmented_control(
        "Zeitraum", ["Abfahrt ab", "Ankunft bis"], default="Abfahrt ab", key="search_time_type"
    )
    time_from = meta[2].time_input("Von", value=None, key="search_time_from")
    time_to = meta[3].time_input("Bis", value=None, key="search_time_to")

    actions = st.columns([1.4, 1.4, 3])
    clicked_search = actions[0].button(
        "Zug finden", type="primary", icon=":material/search:", key="search_btn", width="content"
    )
    actions[1].button(
        "Zufall", icon=":material/casino:", on_click=_random, key="random_btn", width="content"
    )

    st.html(
        hint_html(
            "Demo-Konten: <b>pasajero1–10@trenes.local</b> · Passwort <b>demo1234</b>"
        )
    )

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
    st.stop()

with st.container(border=True):
    route = (
        f"{params['origin'] or 'beliebiger Bahnhof'} → {params['destination'] or 'beliebiger Bahnhof'}"
        if params
        else ""
    )
    st.html(
        card_head(
            "Verbindungswahl",
            f'<span class="muted">{len(results)} Verbindungen · {route}</span>',
        )
    )

    head = st.columns([1.1, 2.3, 1.4, 0.9, 0.8, 0.9])
    for col, label in zip(head, ["Zug", "Verbindung", "Ab / An", "Dauer", "Frei", ""], strict=False):
        col.html(f'<div class="sn-th">{label}</div>')

    for result in results:
        state = inventory.availability_for_segment(feed, result["segment_id"])
        free = inventory.count_available(state)
        row = st.columns([1.1, 2.3, 1.4, 0.9, 0.8, 0.9], vertical_alignment="center")
        row[0].html(f'<div class="sn-td">{train_kind_html(result["train"])}</div>')
        row[1].html(
            f'<div class="sn-td">{route_html(result["origin_stop"], result["destination_stop"])}</div>'
        )
        row[2].html(
            f'<div class="sn-td">'
            f'{time_html(fmt_clock(result["departure"]), fmt_clock(result["arrival"]))}</div>'
        )
        row[3].html(f'<div class="sn-td">{fmt_duration(result["duration_minutes"])}</div>')
        row[4].html(f'<div class="sn-td">{free_html(free)}</div>')
        if row[5].button(
            "Wagen",
            key=f"open_{result['trip_id']}_{result['segment_id']}",
            help=f"{fmt_money(result['price'])}",
        ):
            open_trip(result["trip_id"], result["segment_id"])
            st.switch_page("app_pages/wagen.py")
