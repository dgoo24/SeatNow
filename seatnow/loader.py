"""Capa de caché sobre Streamlit.

`st.cache_data` serializa el resultado, así que el feed GTFS se devuelve
copiado en cada llamada. Es un dataset pequeño (10 viajes, 24 tramos).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from seatnow import gtfs
from seatnow.config import GTFS_DIR


@st.cache_data(ttl="30m", show_spinner=False)
def load_feed() -> gtfs.Feed:
    return gtfs.load_feed(GTFS_DIR)


def set_active_feed(feed: gtfs.Feed, mode: str = "db") -> None:
    """Guarda el feed de la sesión (datos reales de la DB o demo GTFS)."""
    st.session_state["active_feed"] = feed
    st.session_state["feed_mode"] = mode


def _session_value(key: str, default: Any = None) -> Any:
    """Valor de la sesión (compatible con proxies y con SessionState)."""
    try:
        if key in st.session_state:
            return st.session_state[key]
    except Exception:  # pragma: no cover - fuera de un run de Streamlit
        return default
    return default


def active_feed() -> gtfs.Feed:
    """Feed de la sesión: el de la búsqueda actual o el GTFS de demo."""
    feed = _session_value("active_feed")
    if feed is not None:
        return feed
    return load_feed()


def feed_mode() -> str:
    """'db' si el feed actual viene de la DB API, 'demo' si es el GTFS local."""
    return _session_value("feed_mode") or "demo"


@st.cache_data(ttl="10s", show_spinner=False)
def cached_availability(feed: gtfs.Feed, segment_id: str) -> dict[str, dict]:
    from seatnow import inventory

    return inventory.availability_for_segment(feed, segment_id)
