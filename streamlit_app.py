"""SeatNow · Streamlit app.

Punto de entrada para el despliegue en Streamlit Cloud:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import time

import streamlit as st

from seatnow import inventory, store
from seatnow.loader import load_feed
from seatnow.ui import init_state, render_sidebar

st.set_page_config(
    page_title="SeatNow · Sitzplatz buchen",
    page_icon=":material/train:",
    layout="wide",
)

init_state()
store.init_db()

feed = load_feed()

# Barrido de holds caducados y no-shows (como los timers del backend).
if time.time() - st.session_state.get("_last_sweep", 0) > 30:
    st.session_state["_last_sweep"] = time.time()
    inventory.sweep_expired_holds()
    inventory.sweep_no_show(feed)

render_sidebar(feed)

navigation = st.navigation(
    [
        st.Page("app_pages/buscar.py", title="Verbindung suchen", icon=":material/search:"),
        st.Page("app_pages/wagen.py", title="Wagen & Sitzplatz", icon=":material/event_seat:"),
        st.Page("app_pages/reservas.py", title="Meine Reservierungen", icon=":material/confirmation_number:"),
        st.Page("app_pages/admin.py", title="Administration", icon=":material/admin_panel_settings:"),
    ]
)
navigation.run()
