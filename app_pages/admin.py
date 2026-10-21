"""Página 4 · administración y operación."""

from __future__ import annotations

import random

import streamlit as st

from seatnow import inventory, store
from seatnow.format import fmt_money, seat_label, train_label
from seatnow.loader import load_feed
from seatnow.theme import card_head
from seatnow.ui import require_admin

feed = load_feed()
admin = require_admin()


def _demo_purchase() -> dict | None:
    """Simula una compra de un pasajero simulado en un tramo con plazas libres."""
    passenger = store.get_user_by_email("pasajero1@trenes.local")
    if passenger is None:
        return None

    segment_ids = list(feed.segments)
    random.shuffle(segment_ids)
    for segment_id in segment_ids:
        state = inventory.availability_for_segment(feed, segment_id)
        free = [seat for seat, info in state.items() if info["status"] == "AVAILABLE"]
        if not free:
            continue
        segment = feed.segments[segment_id]
        seat_id = random.choice(free)
        try:
            booking = inventory.hold_seat(
                feed,
                user_id=passenger["id"],
                trip_id=segment.trip_id,
                seat_id=seat_id,
                segment_id=segment_id,
                ttl_seconds=60,
            )
            confirmed_booking = inventory.checkout(feed, booking_id=booking["id"])
        except inventory.InventoryError:
            continue
        return confirmed_booking
    return None


with st.container(border=True):
    st.html(card_head("Administration"))
    st.caption("Betriebsansicht für Disposition, No-Show-Regel und Demo-Buchungen.")

    bookings = store.list_bookings()
    confirmed = [b for b in bookings if b["status"] == "CONFIRMED"]
    pending = [b for b in bookings if b["status"] == "PENDING_PAYMENT"]
    no_show = [b for b in bookings if b["status"] == "NO_SHOW"]
    cancelled = [b for b in bookings if b["status"] == "CANCELLED"]
    revenue = sum(b["total_amount"] for b in confirmed)

    metrics = st.columns(5)
    metrics[0].metric("Züge", len(feed.trips))
    metrics[1].metric("Teilstrecken", len(feed.segments))
    metrics[2].metric("Buchungen", len(bookings))
    metrics[3].metric("Bestätigt", len(confirmed))
    metrics[4].metric("Umsatz", fmt_money(revenue))

    st.markdown("**Auslastung nach Sitzplatz (bestätigte Buchungen)**")
    occupancy: dict[str, int] = {}
    for booking in confirmed:
        _, number = booking["seat_id"].rsplit(":", 1)
        occupancy[number] = occupancy.get(number, 0) + 1
    if occupancy:
        st.bar_chart(dict(sorted(occupancy.items())))
    else:
        st.caption("Noch keine bestätigten Buchungen.")

    st.markdown("**Aktionen**")
    actions = st.columns(3)

    with actions[0]:
        if st.button("No-Show-Sweep", icon=":material/moving:", width="stretch"):
            released = inventory.sweep_no_show(feed)
            st.toast(
                f"{len(released)} Buchung(en) als No-Show freigegeben",
                icon=":material/check_circle:",
            )
            st.rerun()

    with actions[1]:
        if st.button("Abgelaufene Holds freigeben", icon=":material/timer_off:", width="stretch"):
            released = inventory.sweep_expired_holds()
            st.toast(f"{released} Hold(s) freigegeben", icon=":material/check_circle:")
            st.rerun()

    with actions[2]:
        if st.button("Demo-Kauf simulieren", icon=":material/science:", width="stretch"):
            created = _demo_purchase()
            if created is None:
                st.warning("Keine freien Plätze verfügbar.", icon=":material/block:")
            else:
                st.toast(
                    f"Demo-Kauf: {seat_label(created['seat_id'])} · "
                    f"{created['segment_id'].split(':')[0]}",
                    icon=":material/shopping_cart_checkout:",
                )
                st.rerun()

with st.container(border=True):
    st.html(card_head("Buchungen", f'<span class="muted">{len(bookings)} Einträge</span>'))
    if not bookings:
        st.caption("Noch keine Buchungen vorhanden.")
    else:
        rows = []
        for booking in bookings[:200]:
            segment = feed.segments.get(booking["segment_id"])
            trip = feed.trips.get(booking["trip_id"])
            rows.append(
                {
                    "Buchung": booking["id"][:8].upper(),
                    "Zug": f"{train_label(trip.route_name)} {trip.route_name}"
                    if trip
                    else booking["trip_id"],
                    "Teilstrecke": f"{segment.origin_stop} → {segment.destination_stop}"
                    if segment
                    else "–",
                    "Sitzplatz": seat_label(booking["seat_id"]),
                    "Status": booking["status"],
                    "Betrag": booking["total_amount"],
                    "Erstellt": booking["created_at"][:16].replace("T", " "),
                    "Check-in": "ja" if booking["is_checked_in"] else "nein",
                }
            )
        st.dataframe(
            rows,
            hide_index=True,
            column_config={
                "Betrag": st.column_config.NumberColumn("Betrag", format="%.2f €"),
                "Status": st.column_config.TextColumn("Status"),
            },
        )
        st.caption(
            f"{len(pending)} offene Zahlung(en) · {len(no_show)} No-Show · {len(cancelled)} storniert"
        )
