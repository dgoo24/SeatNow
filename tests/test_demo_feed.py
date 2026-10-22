"""Regresión del modo demo (feed GTFS local) y del flujo de la app."""

from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from seatnow import gtfs, inventory, search
from seatnow.config import GTFS_DIR
from seatnow.loader import load_feed


def test_demo_feed_has_trips_and_segments() -> None:
    feed = load_feed()
    assert len(feed.trips) == 10
    assert len(feed.segments) == 24
    assert feed.stop_names


def test_demo_search_returns_connections() -> None:
    feed = load_feed()
    origin = feed.stop_names[0]
    destination = feed.stop_names[-1]
    results = search.search_trains(feed, origin=origin, destination=destination)
    assert results
    first = results[0]
    assert first["origin_stop"] == origin
    assert first["destination_stop"] == destination
    assert first["total_seats"] == 50


def test_demo_availability_has_50_seats() -> None:
    feed = load_feed()
    segment_id = next(iter(feed.segments))
    state = inventory.availability_for_segment(feed, segment_id)
    assert len(state) == 50
    assert inventory.count_available(state) == 50


def test_trip_snapshot_roundtrip() -> None:
    feed = load_feed()
    segment_id = next(iter(feed.segments))
    segment = feed.segments[segment_id]
    raw = inventory.trip_snapshot(feed, segment.trip_id, segment_id)
    data = json.loads(raw)
    assert data["origin_stop"] == segment.origin_stop
    assert data["destination_stop"] == segment.destination_stop
    assert inventory.booking_snapshot({"details": raw})["departure"] == segment.departure
    assert inventory.booking_snapshot({}) == {}


def test_gtfs_loader_reads_local_directory() -> None:
    feed = gtfs.load_feed(GTFS_DIR)
    assert feed.trips
    assert feed.segments


def test_app_home_renders() -> None:
    script = Path(__file__).resolve().parent.parent / "streamlit_app.py"
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception
    labels = [button.label for button in at.button]
    assert "Zug finden" in labels
