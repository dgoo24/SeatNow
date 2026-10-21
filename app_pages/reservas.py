"""Página 3 · reservas del usuario."""

from __future__ import annotations

from html import escape

import streamlit as st

from seatnow import inventory, store
from seatnow.format import fmt_clock, fmt_money, seat_label, train_label
from seatnow.loader import load_feed
from seatnow.qr import qr_png
from seatnow.theme import card_head, qr_box_html
from seatnow.ui import require_user, status_badge

feed = load_feed()
user = require_user()

bookings = store.list_bookings(user_id=user["id"])

with st.container(border=True):
    st.html(card_head("Meine Reservierungen", f'<span class="muted">{len(bookings)} Buchungen</span>'))

    if not bookings:
        st.info(
            "Du hast noch keine Reservierung. Suche eine Verbindung und reserviere einen Sitzplatz.",
            icon=":material/event_seat:",
        )
        st.stop()

    filters = ["Alle", "Bestätigt", "Offen", "Storniert"]
    selected_filter = st.segmented_control(
        "Status", filters, default="Alle", key="booking_filter", selection_mode="single"
    )

status_map = {
    "Bestätigt": {"CONFIRMED"},
    "Offen": {"PENDING_PAYMENT"},
    "Storniert": {"CANCELLED", "FAILED", "NO_SHOW"},
}

visible = [
    booking
    for booking in bookings
    if selected_filter == "Alle" or booking["status"] in status_map[selected_filter]
]

st.caption(f"{len(visible)} von {len(bookings)} Reservierungen")

for booking in visible:
    segment = feed.segments.get(booking["segment_id"])
    trip = feed.trips.get(booking["trip_id"])
    if segment is None or trip is None:
        continue

    with st.container(border=True):
        head = st.columns([3, 2, 1.4, 1], vertical_alignment="center")
        head[0].markdown(f"**{train_label(trip.route_name)} · {trip.route_name}**")
        head[1].markdown(f"{segment.origin_stop} → {segment.destination_stop}")
        head[2].markdown(f"{fmt_clock(segment.departure)} – {fmt_clock(segment.arrival)}")
        head[3].markdown(status_badge(booking["status"]))

        st.html(
            '<div class="sn-kv"><b>Buchung</b>'
            f"<span>{escape(booking['id'][:8].upper())}</span></div>"
            '<div class="sn-kv"><b>Sitzplatz</b>'
            f"<span>{escape(seat_label(booking['seat_id']))}</span></div>"
            '<div class="sn-kv"><b>Betrag</b>'
            f"<span>{escape(fmt_money(booking['total_amount']))}</span></div>"
            '<div class="sn-kv"><b>Erstellt</b>'
            f"<span>{escape(booking['created_at'][:10])}</span></div>"
        )
        if booking.get("failure_reason"):
            st.caption(f"Hinweis: {booking['failure_reason']}")

        body = st.columns([1.2, 2, 1], vertical_alignment="center")
        with body[0]:
            if booking["status"] == "CONFIRMED":
                png = qr_png(booking.get("qr_payload") or "")
                if png is not None:
                    with st.container(key=f"qrbox_{booking['id'][:8]}"):
                        st.image(png, width=120)
                else:
                    st.code(booking.get("qr_payload") or "–")
            else:
                st.caption("QR-Code wird nach der Zahlung erzeugt.")

        with body[1]:
            if booking["status"] == "CONFIRMED":
                st.html(qr_box_html(booking.get("qr_payload") or "–"))
            if booking["status"] == "CONFIRMED" and not booking["is_checked_in"]:
                qr_input = st.text_input(
                    "QR-Code zum Check-in",
                    key=f"qr_{booking['id']}",
                    placeholder=booking.get("qr_payload") or "",
                    label_visibility="collapsed",
                )
                if st.button(
                    "Check-in", icon=":material/qr_code_scanner:", key=f"ci_{booking['id']}"
                ):
                    try:
                        inventory.check_in(
                            booking["id"],
                            qr_payload=qr_input or None,
                            user_id=user["id"],
                        )
                    except inventory.InventoryError as error:
                        st.error(str(error), icon=":material/error:")
                    else:
                        st.toast("Check-in erfolgreich", icon=":material/check_circle:")
                        st.rerun()
            elif booking["is_checked_in"]:
                st.success("Eingecheckt", icon=":material/check_circle:")

        with body[2]:
            if booking["status"] in ("CONFIRMED", "PENDING_PAYMENT") and st.button(
                "Stornieren", icon=":material/cancel:", key=f"cancel_{booking['id']}"
            ):
                inventory.cancel_booking(booking["id"], user_id=user["id"])
                st.toast("Reservierung storniert", icon=":material/cancel:")
                st.rerun()
