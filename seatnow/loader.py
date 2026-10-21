"""Capa de caché sobre Streamlit.

`st.cache_data` serializa el resultado, así que el feed GTFS se devuelve
copiado en cada llamada. Es un dataset pequeño (10 viajes, 24 tramos).
"""

from __future__ import annotations

import streamlit as st

from seatnow import gtfs
from seatnow.config import GTFS_DIR


@st.cache_data(ttl="30m", show_spinner=False)
def load_feed() -> gtfs.Feed:
    return gtfs.load_feed(GTFS_DIR)


@st.cache_data(ttl="10s", show_spinner=False)
def cached_availability(feed: gtfs.Feed, segment_id: str) -> dict[str, dict]:
    from seatnow import inventory

    return inventory.availability_for_segment(feed, segment_id)
