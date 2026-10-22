"""SeatNow · Streamlit app.

Punto de entrada para el despliegue en Streamlit Cloud:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import time

import streamlit as st

from seatnow import dbapi, inventory, store
from seatnow.loader import active_feed
from seatnow.theme import footer_html, inject
from seatnow.ui import init_state, render_header, render_nav

st.set_page_config(
    page_title="SeatNow · Sitzplatz buchen",
    page_icon=":material/train:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_state()
store.init_db()

feed = active_feed()

# Barrido de holds caducados y no-shows (como los timers del backend).
if time.time() - st.session_state.get("_last_sweep", 0) > 30:
    st.session_state["_last_sweep"] = time.time()
    inventory.sweep_expired_holds()
    inventory.sweep_no_show(feed)

# Estilo del dashboard (backend/public/index.html).
inject()

# La navegación se dibuja a mano dentro de la cabecera (sin barra lateral).
PAGES = [
    st.Page("app_pages/buscar.py", title="Verbindung suchen", icon=":material/search:"),
    st.Page("app_pages/wagen.py", title="Wagen & Sitzplatz", icon=":material/event_seat:"),
    st.Page("app_pages/reservas.py", title="Meine Reservierungen", icon=":material/confirmation_number:"),
    st.Page("app_pages/admin.py", title="Administration", icon=":material/admin_panel_settings:"),
]

page = st.navigation(PAGES, position="hidden")

source = "db" if dbapi.available() else "demo"
render_header(feed, source)

# La navegación vive dentro del dashboard (no en la barra lateral).
render_nav(PAGES, page)

page.run()

st.html(footer_html(source))
